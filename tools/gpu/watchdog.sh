#!/usr/bin/env bash
# Keep TransformX running: every minute, check that the website and each model server answer, and
# start again whatever stopped. start_site.sh starts this watchdog; one runs at a time.
#
#   log:     /workspace/transformx-watchdog.log   (each problem and each restart, with the time)
#   pause:   bash tools/gpu/stop_site.sh          (creates /workspace/transformx-paused; the watchdog
#            leaves everything alone while that file exists; start_site.sh removes it)
#
# A problem must be seen on 3 checks in a row (about 3 minutes) before anything is restarted, so a
# server busy with a large job is not restarted for one slow answer. A stopped or silent website is
# restarted with start_site.sh --restart (a job running at that moment shows as interrupted and can
# be started again); a model server that stopped is started again by start_worker.sh --servers-only,
# which leaves the servers that still run alone.
#
# TRANSFORMX_WATCHDOG_SECONDS (60) sets the time between checks, TRANSFORMX_WATCHDOG_FAILURES (3) the
# checks in a row, TRANSFORMX_MIN_FREE_GB (5) the free disk space below which the log warns.
set -uo pipefail
app="$(cd "$(dirname "$0")/../.." && pwd)"
log="${TRANSFORMX_WATCHDOG_LOG:-/workspace/transformx-watchdog.log}"
pause="${TRANSFORMX_PAUSE_FILE:-/workspace/transformx-paused}"
every="${TRANSFORMX_WATCHDOG_SECONDS:-60}"
needed="${TRANSFORMX_WATCHDOG_FAILURES:-3}"
min_free_gb="${TRANSFORMX_MIN_FREE_GB:-5}"
servers_env="${TRANSFORMX_SERVERS_ENV:-/workspace/servers.env}"
port="${TRANSFORMX_PORT:-8080}"
host="${TRANSFORMX_HOST:-127.0.0.1}"
case "$host" in 0.0.0.0|::|'') host=127.0.0.1 ;; esac
site_pattern='^[^ ]*python[0-9.]* (-u )?tools/service/local_server[.]py'

note() {
  # Keep the log small: past 5 MB it moves to .1 (one older copy).
  [ -f "$log" ] && [ "$(stat -c %s "$log" 2>/dev/null || echo 0)" -gt 5000000 ] && mv -f "$log" "$log.1"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$log"
}

# One watchdog at a time. Everything started from here gets the lock closed (9>&-): a website or
# model server started by the watchdog must not keep holding it after the watchdog stops.
exec 9>"${TRANSFORMX_WATCHDOG_LOCK:-/tmp/transformx-watchdog-$(id -u).lock}"
flock -n 9 || exit 0
note "watchdog started (checks every ${every} s)"

site_answers() {  # any HTTP answer counts, a sign-in request (401) too; 000 is no answer
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "http://$host:$port/v1/system" 9>&-)
  [ "$code" != 000 ]
}

model_ports() {  # the local model servers the website was started with (servers.env)
  [ -f "$servers_env" ] || return 0
  grep -E '^(export )?TRANSFORMX_(PII_ENDPOINT|TRANSLATION_ENDPOINT|HIGH_QUALITY_TRANSLATION)=' "$servers_env" \
    | grep -oE '(127\.0\.0\.1|localhost):[0-9]+' | cut -d: -f2 | sort -u
}

disk_warned=0
fails=0
while sleep "$every"; do
  if [ -e "$pause" ]; then fails=0; continue; fi
  site=""; models=""
  if ! pgrep -f "$site_pattern" >/dev/null 9>&-; then site="website stopped"
  elif ! site_answers; then site="website not answering"; fi
  for p in $(model_ports); do
    # A sleeping vLLM server (GPU sharing) still answers here.
    curl -fsS --max-time 30 "http://127.0.0.1:$p/v1/models" >/dev/null 2>&1 9>&- || models="$models $p"
  done
  free_kb=$(df -Pk "$app" 2>/dev/null | awk 'NR==2{print $4}')
  if [ -n "$free_kb" ] && [ "$free_kb" -lt $((min_free_gb * 1024 * 1024)) ]; then
    [ "$disk_warned" = 0 ] && note "warning: less than ${min_free_gb} GB free on the disk of $app"
    disk_warned=1
  else
    disk_warned=0
  fi

  problem="$site${models:+${site:+; }model server not answering on port$models}"
  if [ -z "$problem" ]; then
    [ "$fails" -gt 0 ] && note "all answering again"
    fails=0; continue
  fi
  fails=$((fails + 1))
  note "check $fails/$needed: $problem"
  [ "$fails" -lt "$needed" ] && continue
  fails=0
  if [ -n "$site" ]; then
    note "restarting the website and any stopped model server (start_site.sh --restart)"
    bash "$app/tools/gpu/start_site.sh" --restart >> "$log" 2>&1 9>&- || note "start_site.sh failed; see the lines above"
  else
    note "starting the stopped model server(s) (start_worker.sh --servers-only)"
    TRANSFORMX_SERVERS_ENV="$servers_env" bash "$app/tools/gpu/start_worker.sh" --servers-only >> "$log" 2>&1 9>&- \
      || note "start_worker.sh failed; see the lines above"
  fi
done
