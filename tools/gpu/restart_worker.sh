#!/usr/bin/env bash
# Restart the site worker so it runs the code now in this folder. Run it after every code update.
#
# usage: bash tools/gpu/restart_worker.sh [--now]
#
# Job processes start from pre-loaded parents (transformation/preload.py) that keep the code they
# loaded. After an update they must be restarted too, or a job mixes old and new modules (seen
# 2026-11-19: "messages() got an unexpected keyword argument"). This script waits until no job is
# running (unless --now), stops the worker and the pre-loaded parents, then starts the worker again
# with tools/gpu/start_worker.sh (model servers that are running are left alone).
set -euo pipefail
app="$(cd "$(dirname "$0")/../.." && pwd)"
worker_pattern='^[^ ]*python[0-9.]* (-u )?tools/service/cloud_worker[.]py run'
job_pattern='^[^ ]*python[0-9.]* (-u )?-m backend[.]engine_job '
now=0; if [ "${1:-}" = "--now" ]; then now=1; shift; fi
if [ "$now" = 0 ]; then
  for _ in $(seq 1 360); do
    pgrep -f "$job_pattern" >/dev/null || break
    echo "A job is running; waiting for it to finish..."; sleep 10
  done
  if pgrep -f "$job_pattern" >/dev/null; then echo "A job is still running after an hour; use --now to stop it."; exit 1; fi
fi
pkill -f "$worker_pattern" 2>/dev/null || true
for _ in $(seq 1 30); do pgrep -f "$worker_pattern" >/dev/null || break; sleep 1; done
pkill -f '^[^ ]*python[0-9.]* -m transformation[.]preload ' 2>/dev/null || true
rm -f /tmp/transformx-preload-"$(id -u)"/*.sock 2>/dev/null || true
# Python caches of the updated files are rebuilt on import.
exec bash "$app/tools/gpu/start_worker.sh" "$@"
