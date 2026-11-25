#!/usr/bin/env bash
# Start TransformX on this GPU server: the model servers, then the website with its engine.
# Safe to run again: anything already running is left alone. Set up the server once first:
#   bash tools/gpu/setup_transformx.sh pii && bash tools/gpu/setup_full_pipeline.sh && bash tools/gpu/setup_vllm.sh
#
# usage: bash tools/gpu/start_site.sh [--restart]   (--restart after changing configs/transformx.toml)
#        then, on your own computer:
#        ssh -L 8080:localhost:8080 -p <port> root@<server>    and open http://localhost:8080
#
# TRANSFORMX_HOST (default 127.0.0.1) and TRANSFORMX_PORT (default 8080) choose where the website
# listens; TRANSFORMX_PASSWORD asks every visitor for a password (needed before listening on 0.0.0.0).
set -euo pipefail
app="$(cd "$(dirname "$0")/../.." && pwd)"
python="${TRANSFORMX_PYTHON:-/workspace/paddle-env/bin/python}"
log="${TRANSFORMX_SITE_LOG:-/workspace/transformx-site.log}"
host="${TRANSFORMX_HOST:-127.0.0.1}"
port="${TRANSFORMX_PORT:-8080}"
[ -x "$python" ] || { echo "GPU Python not found: $python (run the setup scripts first)."; exit 1; }
[ -f "$app/frontend/out/workspace.html" ] || { echo "Website not built: run 'npm --prefix frontend ci && npm --prefix frontend run build'."; exit 1; }
if [ "$host" != 127.0.0.1 ] && [ "$host" != localhost ] && [ -z "${TRANSFORMX_PASSWORD:-}" ]; then
  echo "Set TRANSFORMX_PASSWORD before listening on $host: without it anyone who reaches the address can use the server."
  exit 1
fi

site_pattern='^[^ ]*python[0-9.]* (-u )?tools/service/local_server[.]py'
servers_env="${TRANSFORMX_SERVERS_ENV:-/workspace/servers.env}"
# Starting the site ends a pause made by stop_site.sh.
rm -f "${TRANSFORMX_PAUSE_FILE:-/workspace/transformx-paused}"

# The watchdog (tools/gpu/watchdog.sh) starts whatever stops; TRANSFORMX_WATCHDOG=0 leaves it off.
# Only one runs: starting it again when one runs does nothing.
start_watchdog() {
  [ "${TRANSFORMX_WATCHDOG:-1}" = 0 ] && return 0
  command -v flock >/dev/null || { echo "flock is missing: the watchdog was not started."; return 0; }
  (
    set -a
    # shellcheck disable=SC1090
    [ -f "$servers_env" ] && . "$servers_env"
    set +a
    TRANSFORMX_SERVERS_ENV="$servers_env" nohup bash "$app/tools/gpu/watchdog.sh" >/dev/null 2>&1 &
  )
  echo "Watchdog on (log: ${TRANSFORMX_WATCHDOG_LOG:-/workspace/transformx-watchdog.log})."
}

# --restart: stop the website first, so it reads changed settings (configs/transformx.toml). Running
# jobs are stopped too and show as interrupted. Model servers keep running unless their settings changed.
if [ "${1:-}" = "--restart" ]; then
  pkill -f "$site_pattern" 2>/dev/null || true
  for _ in $(seq 1 15); do pgrep -f "$site_pattern" >/dev/null || break; sleep 1; done
fi
if pgrep -f "$site_pattern" >/dev/null; then
  echo "TransformX is already running (log: $log)."
  start_watchdog
  exit 0
fi

# The model servers (vLLM: translation and PII) and their settings in servers.env.
TRANSFORMX_SERVERS_ENV="$servers_env" bash "$app/tools/gpu/start_worker.sh" --servers-only
set -a
# shellcheck disable=SC1090
. "$servers_env"
set +a

# BabelDOC's fonts, layout model and character maps, downloaded and checked before any job runs:
# BabelDOC writes a download straight to its final name, and on a new server two jobs side by side
# read a font the other was still writing ("FT_New_Memory_Face: unknown file format", 2026-11-24).
babeldoc_python="${TRANSFORMX_BABELDOC_PYTHON:-/workspace/transformx-babeldoc/bin/python}"
if [ -x "$babeldoc_python" ]; then
  "$babeldoc_python" -c 'from babeldoc.assets.assets import warmup; warmup()' >/dev/null 2>&1     || echo "BabelDOC assets could not be downloaded now; the first jobs will fetch them."
fi

# The website's server (FastAPI) in the GPU Python environment.
if ! "$python" -c 'import fastapi, uvicorn, multipart' 2>/dev/null; then
  echo "Installing the website server (FastAPI)..."
  if command -v uv >/dev/null; then
    uv pip install --python "$python" 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'python-multipart>=0.0.18,<1'
  else
    "$python" -m pip install 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'python-multipart>=0.0.18,<1'
  fi
fi

# Jobs run side by side when the GPU has room for both models and two jobs' OCR and layout work
# (40 GB or more: L40S, A100, H100); [service] parallel_jobs in transformx.toml sets it explicitly.
if [ -z "${TRANSFORMX_SERVICE_WORKERS:-}" ]; then
  gpu_mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
  export TRANSFORMX_SERVICE_WORKERS=1
  [ "${gpu_mib:-0}" -ge 40000 ] && export TRANSFORMX_SERVICE_WORKERS=2
fi

# A new start loads the current code: stop pre-load servers left from a previous start.
pkill -f '^[^ ]*python[0-9.]* -m transformation[.]preload ' 2>/dev/null || true
rm -f /tmp/transformx-preload-"$(id -u)"/*.sock 2>/dev/null || true
cd "$app"
PYTHONPATH="$app" nohup "$python" -u tools/service/local_server.py --host "$host" --port "$port" > "$log" 2>&1 &
for _ in $(seq 1 120); do
  if grep -q "TransformX website:" "$log" 2>/dev/null; then
    grep "Server ready for" "$log" | tail -1
    echo "TransformX is running on port $port (log: $log)."
    [ "$host" = 127.0.0.1 ] && echo "On your computer: ssh -L $port:localhost:$port <your usual ssh options>, then open http://localhost:$port"
    start_watchdog
    exit 0
  fi
  if ! pgrep -f "$site_pattern" >/dev/null; then echo "TransformX stopped; last lines:"; tail -8 "$log"; exit 1; fi
  sleep 2
done
echo "TransformX started but has not reported ready yet; check $log."
start_watchdog
