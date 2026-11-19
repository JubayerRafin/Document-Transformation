#!/usr/bin/env bash
# Start (or confirm) the TransformX site worker on a GPU pod. Safe to run again: anything
# already running is left alone. Run setup_transformx.sh once on a new pod first.
#
# usage: bash tools/gpu/start_worker.sh [config path, default configs/gpu.worker.local.json]
#        bash tools/gpu/start_worker.sh --servers-only
# The config is the gpu.worker.local.json downloaded from the site's Workers panel.
# --servers-only starts the model servers and writes their settings (endpoints, model names, no
# keys) to /workspace/servers.env, without the site worker: for running the engine some other way.
set -euo pipefail
servers_only=0
if [ "${1:-}" = "--servers-only" ]; then servers_only=1; shift; fi
app="$(cd "$(dirname "$0")/../.." && pwd)"
python="${TRANSFORMX_PYTHON:-/workspace/paddle-env/bin/python}"
log="${TRANSFORMX_WORKER_LOG:-/workspace/cloud-worker.log}"
[ -x "$python" ] || { echo "GPU Python not found: $python (run tools/gpu/setup_transformx.sh pii first)."; exit 1; }

# configs/transformx.toml (or $TRANSFORMX_SETTINGS), if present: its values become environment
# defaults here, so everything below sees them. Variables already set in the environment win.
settings_env=$(cd "$app" && PYTHONPATH="$app" "$python" -m transformation.settings env) || {
  echo "Fix the settings file first (python -m transformation.settings check)."; exit 1; }
eval "$settings_env"
config="${1:-${TRANSFORMX_WORKER_CONFIG:-$app/configs/gpu.worker.local.json}}"
# A model server set to another machine is used as it is; only local ones are started here.
local_server() { case "${1:-}" in ""|http://127.0.0.1:"$2"*|http://localhost:"$2"*) return 0;; *) return 1;; esac; }

if [ "$servers_only" = 0 ]; then
  [ -f "$config" ] || { echo "Worker config not found: $config (download it from the site's Workers panel)."; exit 1; }
  chmod 600 "$config"
fi

if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  # Same settings as setup_transformx.sh: parallel requests, flash attention, 8-bit KV cache.
  nohup env OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/workspace/ollama-models \
    OLLAMA_NUM_PARALLEL="${TRANSFORMX_OLLAMA_PARALLEL:-8}" OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=30m \
    ollama serve > /workspace/ollama.log 2>&1 &
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
fi
curl -fsS http://127.0.0.1:11434/api/version >/dev/null || { echo "Ollama did not start; see /workspace/ollama.log."; exit 1; }

# A dedicated GPU keeps the model loaded: every engine request sends this keep-alive, so the
# default 30 minutes would unload it after a quiet period and the next job would wait ~90 s.
export TRANSFORMX_OLLAMA_KEEP_ALIVE="${TRANSFORMX_OLLAMA_KEEP_ALIVE:--1h}"

# PII on vLLM when it is installed (tools/gpu/setup_vllm.sh): the same Qwen3.5-9B model in FP8,
# with parallel decoding about ten times faster than Ollama for this model (measured 2026-11-15:
# 2.8 s/page instead of 12.6 s on the 52-document set). TRANSFORMX_PII_SERVER=ollama keeps Ollama.
vllm="${TRANSFORMX_VLLM:-/workspace/vllm-env/bin/vllm}"
vllm_model="${TRANSFORMX_VLLM_MODEL:-/workspace/models/qwen3.5-9b-fp8}"
vllm_name=qwen3.5-9b-fp8
# TranslateGemma for vLLM (tools/gpu/setup_vllm.sh), served under the engine's model name.
tg_model="${TRANSFORMX_TRANSLATION_VLLM_MODEL:-/workspace/models/translategemma-12b}"
gpu_mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
# Each server gets the same absolute memory on any card (shares were tuned on a 24 GB RTX 4090).
pii_share=$(awk -v m="${gpu_mib:-24564}" 'BEGIN{s=16800/m; if(s>0.68)s=0.68; printf "%.3f", s}')
tg_share=$(awk -v m="${gpu_mib:-24564}" 'BEGIN{s=17400/m; if(s>0.71)s=0.71; printf "%.3f", s}')
# Under 40 GB the two models do not fit together: each job stage gets the GPU in turn
# (transformation/llm/gpu_share.py, vLLM sleep mode: about 1 s to switch).
shared=0; [ "${gpu_mib:-0}" -lt 40000 ] && shared=1
# A sleeping server's weights wait in pinned host memory (level 1, ~1 s to wake) unless the machine's
# memory limit is too small for that; then they are reloaded from disk on wake (level 2, ~10 s). Two
# parked models exceeded a 41 GB pod's limit; 80 GB leaves room for them and the engine processes.
mem_limit=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo max)
[ "$mem_limit" = max ] && mem_limit=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo)
tg_level=2; [ "${mem_limit:-0}" -ge 80000000000 ] && tg_level=1
servers=""

wait_for() {  # port, log
  for _ in $(seq 1 150); do curl -fsS "http://127.0.0.1:$1/v1/models" >/dev/null 2>&1 && return 0; sleep 2; done
  echo "vLLM on port $1 did not start; see $2."; exit 1
}

if [ "${TRANSFORMX_PII_SERVER:-auto}" != ollama ] && local_server "${TRANSFORMX_PII_ENDPOINT:-}" 8000 \
    && [ -x "$vllm" ] && [ -f "$vllm_model/config.json" ]; then
  if ! curl -fsS http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
    [ "$shared" = 1 ] && curl -fsS -X POST "http://127.0.0.1:8010/sleep?level=1" >/dev/null 2>&1 || true
    # Loopback only. MTP drafts tokens ahead (lossless with greedy decoding) for about 1.5x faster
    # answers. Sleep mode (its endpoints need dev mode; loopback only) lets translation borrow the GPU.
    nohup env PATH="$(dirname "$vllm"):$PATH" VLLM_SERVER_DEV_MODE=1 "$vllm" serve "$vllm_model" --served-model-name "$vllm_name" \
      --host 127.0.0.1 --port 8000 --max-model-len 8192 --max-num-seqs 32 --enable-prefix-caching \
      --gpu-memory-utilization "${TRANSFORMX_VLLM_GPU_SHARE:-$pii_share}" --limit-mm-per-prompt '{"image":0,"video":0}' \
      --speculative-config '{"method":"mtp","num_speculative_tokens":2}' --enable-sleep-mode > /workspace/vllm.log 2>&1 &
    echo "Starting the PII model server (first start compiles kernels, about 2 minutes)..."
    wait_for 8000 /workspace/vllm.log
  fi
  servers=http://127.0.0.1:8000
  export TRANSFORMX_PII_ENDPOINT=http://127.0.0.1:8000/v1 TRANSFORMX_PII_MODEL="$vllm_name"
  echo "PII model $vllm_name served by vLLM."
fi

if [ "${TRANSFORMX_TRANSLATION_SERVER:-auto}" != ollama ] && local_server "${TRANSFORMX_TRANSLATION_ENDPOINT:-}" 8010 \
    && [ -x "$vllm" ] && [ -f "$tg_model/config.json" ]; then
  if ! curl -fsS http://127.0.0.1:8010/v1/models >/dev/null 2>&1; then
    # Port 8010: RunPod images proxy 8001 to 8000. The PII server sleeps while this one claims memory.
    [ "$shared" = 1 ] && curl -fsS -X POST "http://127.0.0.1:8000/sleep?level=1" >/dev/null 2>&1 || true
    nohup env PATH="$(dirname "$vllm"):$PATH" VLLM_SERVER_DEV_MODE=1 "$vllm" serve "$tg_model" --served-model-name "translategemma:12b" \
      --host 127.0.0.1 --port 8010 --quantization fp8 --max-model-len 4096 --max-num-seqs 32 --enable-prefix-caching \
      --gpu-memory-utilization "${TRANSFORMX_TRANSLATION_GPU_SHARE:-$tg_share}" --limit-mm-per-prompt '{"image":0}' \
      --chat-template "$app/tools/gpu/translategemma.jinja" --enable-sleep-mode > /workspace/vllm-translation.log 2>&1 &
    echo "Starting the translation model server (about 2 minutes)..."
    wait_for 8010 /workspace/vllm-translation.log
    if [ "$shared" = 1 ] && [ -n "$servers" ]; then
      curl -fsS -X POST "http://127.0.0.1:8010/sleep?level=$tg_level" >/dev/null && curl -fsS -X POST http://127.0.0.1:8000/wake_up >/dev/null
    fi
  fi
  # "#2" marks a level-2 server: gpu_share.py reloads its weights on wake (see tg_level above).
  marker=""; [ "$tg_level" = 2 ] && marker="#2"
  servers="${servers:+$servers,}http://127.0.0.1:8010$marker"
  # A vLLM translation server serves many requests at once (measured: 16 parallel calls).
  export TRANSFORMX_TRANSLATION_ENDPOINT=http://127.0.0.1:8010/v1 \
    TRANSFORMX_TRANSLATION_WORKERS="${TRANSFORMX_TRANSLATION_WORKERS:-16}" TRANSFORMX_TRANSLATION_QPS="${TRANSFORMX_TRANSLATION_QPS:-64}"
  echo "Translation model translategemma:12b served by vLLM."
fi

if [ -n "$servers" ]; then
  plain="${servers//#2/}"   # addresses without the sleep-level marker
  # Keep remote servers listed in the settings file or the environment.
  export TRANSFORMX_OPENAI_COMPATIBLE="$plain${TRANSFORMX_OPENAI_COMPATIBLE:+,$TRANSFORMX_OPENAI_COMPATIBLE}"
  if [ "$shared" = 1 ]; then
    for server in ${plain//,/ }; do
      curl -fsS "$server/is_sleeping" >/dev/null 2>&1 || { echo "$server runs without sleep mode; stop it and run this script again."; exit 1; }
    done
    export TRANSFORMX_GPU_SWAP="$servers"
    echo "Model servers share the GPU (${gpu_mib} MiB) and take turns on it."
  fi
fi

if [ -z "${TRANSFORMX_PII_ENDPOINT:-}" ]; then
  model="${TRANSFORMX_PII_MODEL:-qwen3.5:9b}"
  # Load and warm the model now, with the engine's exact context size (a different size reloads it),
  # so the first customer job does not pay the model load and first-use GPU setup.
  curl -fsS --max-time 300 http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"$model\",\"prompt\":\"ok\",\"stream\":false,\"keep_alive\":\"$TRANSFORMX_OLLAMA_KEEP_ALIVE\",\"options\":{\"temperature\":0,\"num_ctx\":8192,\"num_predict\":1}}" \
    >/dev/null && echo "Model $model loaded and warm." || echo "Model warm-up failed; the first job will load it."
fi

# Match the worker process itself (an interpreter running the script), not another command
# that only mentions it, such as a remote shell started to run this script.
worker_pattern='^[^ ]*python[0-9.]* (-u )?tools/service/cloud_worker[.]py run'
# Translation runs BabelDOC and the document engine in the environments setup_full_pipeline.sh creates.
if [ -x /workspace/transformx-babeldoc/bin/python ] && [ -x /workspace/transformx-engine/bin/python ]; then
  export TRANSFORMX_BABELDOC_PYTHON=/workspace/transformx-babeldoc/bin/python
  export TRANSFORMX_ENGINE_PYTHON=/workspace/transformx-engine/bin/python
  # BabelDOC's page-layout model on the GPU when its CUDA build is installed (transformation/pdf/layout_device.py).
  if "$TRANSFORMX_BABELDOC_PYTHON" -c "import onnxruntime as o, sys; sys.exit('CUDAExecutionProvider' not in o.get_available_providers())" 2>/dev/null; then
    export TRANSFORMX_LAYOUT_DEVICE=cuda
  fi
fi
# Engine and BabelDOC job processes start from pre-loaded parents (transformation/preload.py).
export TRANSFORMX_PRELOAD="${TRANSFORMX_PRELOAD:-1}"

if [ "$servers_only" = 1 ]; then
  # Settings for the engine on this machine; API keys are never written here.
  env | grep '^TRANSFORMX_' | grep -v '_API_KEY' | sort > "${TRANSFORMX_SERVERS_ENV:-/workspace/servers.env}"
  echo "Model servers ready; settings in ${TRANSFORMX_SERVERS_ENV:-/workspace/servers.env}."
  exit 0
fi

if pgrep -f "$worker_pattern" >/dev/null; then
  echo "Worker already running (log: $log)."
  exit 0
fi
# A new worker loads the current code: stop pre-load servers left from a previous start.
pkill -f '^[^ ]*python[0-9.]* -m transformation[.]preload ' 2>/dev/null || true
rm -f /tmp/transformx-preload-"$(id -u)"/*.sock 2>/dev/null || true
cd "$app"
PYTHONPATH="$app" nohup "$python" -u tools/service/cloud_worker.py run --config "$config" > "$log" 2>&1 &
for _ in $(seq 1 90); do
  grep -q "Worker ready for" "$log" 2>/dev/null && { tail -1 "$log"; echo "Log: $log"; exit 0; }
  if ! pgrep -f "$worker_pattern" >/dev/null; then echo "Worker stopped; last lines:"; tail -5 "$log"; exit 1; fi
  sleep 2
done
echo "Worker started but has not reported ready yet; check $log."
