#!/usr/bin/env bash
# Stop TransformX for maintenance: the watchdog is paused first, so it does not start the website
# again, then the website stops. start_site.sh starts everything again and ends the pause.
#
# usage: bash tools/gpu/stop_site.sh [--now] [--models]
#   waits until no job is running (up to an hour) unless --now; a job stopped by --now shows as
#   interrupted and can be started again.
#   --models also stops the model servers (vLLM) and frees the GPU; starting them again takes a few
#   minutes.
set -euo pipefail
pause="${TRANSFORMX_PAUSE_FILE:-/workspace/transformx-paused}"
site_pattern='^[^ ]*python[0-9.]* (-u )?tools/service/local_server[.]py'
job_pattern='^[^ ]*python[0-9.]* (-u )?-m backend[.]engine_job '
now=0; models=0
for arg in "$@"; do
  case "$arg" in
    --now) now=1 ;;
    --models) models=1 ;;
    *) echo "usage: bash tools/gpu/stop_site.sh [--now] [--models]"; exit 2 ;;
  esac
done

touch "$pause"
echo "Watchdog paused ($pause)."
if [ "$now" = 0 ]; then
  for _ in $(seq 1 360); do
    pgrep -f "$job_pattern" >/dev/null || break
    echo "A job is running; waiting for it to finish (Ctrl+C to stop waiting; the watchdog stays paused)..."
    sleep 10
  done
  if pgrep -f "$job_pattern" >/dev/null; then echo "A job is still running after an hour; use --now to stop it."; exit 1; fi
fi
pkill -f "$site_pattern" 2>/dev/null || true
for _ in $(seq 1 15); do pgrep -f "$site_pattern" >/dev/null || break; sleep 1; done
pkill -f "$job_pattern" 2>/dev/null || true
pkill -f '^[^ ]*python[0-9.]* -m transformation[.]preload ' 2>/dev/null || true
echo "Website stopped."
if [ "$models" = 1 ]; then
  pkill -f 'vllm serve' 2>/dev/null || true
  for _ in $(seq 1 30); do pgrep -f 'vllm serve' >/dev/null || break; sleep 1; done
  echo "Model servers stopped."
fi
echo "Start again with: bash tools/gpu/start_site.sh"
