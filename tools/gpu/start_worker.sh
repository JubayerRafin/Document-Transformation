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

if pgrep -f "tools/service/cloud_worker.py run" >/dev/null; then
  echo "Worker already running (log: $log)."
  exit 0
fi
cd "$app"
PYTHONPATH="$app" nohup "$python" -u tools/service/cloud_worker.py run --config "$config" > "$log" 2>&1 &
for _ in $(seq 1 90); do
  grep -q "Worker ready for" "$log" 2>/dev/null && { tail -1 "$log"; echo "Log: $log"; exit 0; }
  if ! pgrep -f "tools/service/cloud_worker.py run" >/dev/null; then echo "Worker stopped; last lines:"; tail -5 "$log"; exit 1; fi
  sleep 2
done
echo "Worker started but has not reported ready yet; check $log."
