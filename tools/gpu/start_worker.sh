#!/usr/bin/env bash
# Start (or confirm) the TransformX site worker on a GPU pod. Safe to run again: anything
# already running is left alone. Run setup_transformx.sh once on a new pod first.
#
# usage: bash tools/gpu/start_worker.sh [config path, default configs/gpu.worker.local.json]
# The config is the gpu.worker.local.json downloaded from the site's Workers panel.
set -euo pipefail
app="$(cd "$(dirname "$0")/../.." && pwd)"
config="${1:-$app/configs/gpu.worker.local.json}"
python="${TRANSFORMX_PYTHON:-/workspace/paddle-env/bin/python}"
log="${TRANSFORMX_WORKER_LOG:-/workspace/cloud-worker.log}"

[ -f "$config" ] || { echo "Worker config not found: $config (download it from the site's Workers panel)."; exit 1; }
[ -x "$python" ] || { echo "GPU Python not found: $python (run tools/gpu/setup_transformx.sh pii first)."; exit 1; }
chmod 600 "$config"

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
if [ "${TRANSFORMX_PII_SERVER:-auto}" != ollama ] && [ -x "$vllm" ] && [ -f "$vllm_model/config.json" ]; then
  if ! curl -fsS http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
    # Loopback only. The share of GPU memory leaves room for PaddleOCR; MTP drafts tokens ahead
    # (lossless with greedy decoding) for about 1.5x faster answers.
    nohup env PATH="$(dirname "$vllm"):$PATH" "$vllm" serve "$vllm_model" --served-model-name "$vllm_name" \
      --host 127.0.0.1 --port 8000 --max-model-len 8192 --max-num-seqs 32 --enable-prefix-caching \
      --gpu-memory-utilization "${TRANSFORMX_VLLM_GPU_SHARE:-0.68}" --limit-mm-per-prompt '{"image":0,"video":0}' \
      --speculative-config '{"method":"mtp","num_speculative_tokens":2}' > /workspace/vllm.log 2>&1 &
    echo "Starting vLLM (first start compiles kernels, about 2 minutes)..."
    for _ in $(seq 1 150); do curl -fsS http://127.0.0.1:8000/v1/models >/dev/null 2>&1 && break; sleep 2; done
  fi
  curl -fsS http://127.0.0.1:8000/v1/models >/dev/null || { echo "vLLM did not start; see /workspace/vllm.log."; exit 1; }
  export TRANSFORMX_OPENAI_COMPATIBLE=http://127.0.0.1:8000
  export TRANSFORMX_PII_ENDPOINT=http://127.0.0.1:8000/v1 TRANSFORMX_PII_MODEL="$vllm_name"
  echo "PII model $vllm_name served by vLLM."
else
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
if pgrep -f "$worker_pattern" >/dev/null; then
  echo "Worker already running (log: $log)."
  exit 0
fi
cd "$app"
PYTHONPATH="$app" nohup "$python" -u tools/service/cloud_worker.py run --config "$config" > "$log" 2>&1 &
for _ in $(seq 1 90); do
  grep -q "Worker ready for" "$log" 2>/dev/null && { tail -1 "$log"; echo "Log: $log"; exit 0; }
  if ! pgrep -f "$worker_pattern" >/dev/null; then echo "Worker stopped; last lines:"; tail -5 "$log"; exit 1; fi
  sleep 2
done
echo "Worker started but has not reported ready yet; check $log."
