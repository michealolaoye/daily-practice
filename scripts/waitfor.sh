#!/usr/bin/env bash
# Wait until something is ready, then exit: a TCP port accepts connections,
# an HTTP URL answers, a file appears, or a command succeeds. Meant for
# deploy and startup scripts ("don't run migrations until the database is
# up"). Polls with an optional exponential backoff and gives up after a
# timeout. Needs bash and coreutils; `http` also needs curl.
set -uo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: waitfor.sh [options] KIND TARGET...

Kinds:
  tcp HOST:PORT      port accepts a connection
  http URL           URL answers (2xx/3xx, or exactly --status)
  file PATH          path exists
  cmd COMMAND ARGS   command exits 0

Options:
  -t SECONDS   give up after this long (default 30; 0 = try once)
  -i SECONDS   wait between attempts (default 1)
  -b           back off: double the wait after each failure
  -m SECONDS   longest wait when backing off (default 5)
  -s CODE      with http: require exactly this status code
  -q           no output

Exit: 0 ready, 1 timed out, 2 bad usage.
EOF
    exit 2
}

is_number() { [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]]; }

timeout_s=30
interval=1
backoff=0
max_wait=5
want_status=""
quiet=0

while [ $# -gt 0 ]; do
    case "$1" in
        -t) [ $# -ge 2 ] && is_number "$2" || usage; timeout_s="$2"; shift 2 ;;
        -i) [ $# -ge 2 ] && is_number "$2" || usage; interval="$2"; shift 2 ;;
        -m) [ $# -ge 2 ] && is_number "$2" || usage; max_wait="$2"; shift 2 ;;
        -s) [ $# -ge 2 ] && [[ "$2" =~ ^[0-9]{3}$ ]] || usage; want_status="$2"; shift 2 ;;
        -b) backoff=1; shift ;;
        -q) quiet=1; shift ;;
        -h|--help) usage ;;
        --) shift; break ;;
        -*) usage ;;
        *) break ;;
    esac
done

[ $# -ge 2 ] || usage
kind="$1"
shift

# Each probe returns 0 when the thing is ready. Arguments are always passed
# as separate words and never spliced into a command string.
probe_tcp() {
    local host="${1%:*}" port="${1##*:}"
    timeout 2 bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$host" "$port" 2>/dev/null
}

probe_http() {
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null)" || return 1
    if [ -n "$want_status" ]; then
        [ "$code" = "$want_status" ]
    else
        [ "$code" -ge 200 ] && [ "$code" -lt 400 ]
    fi
}

probe_file() { [ -e "$1" ]; }

probe_cmd() { "$@" >/dev/null 2>&1; }

case "$kind" in
    tcp)
        [ $# -eq 1 ] && [[ "$1" =~ ^[^:]+:[0-9]+$ ]] || usage
        probe=probe_tcp
        ;;
    http)
        [ $# -eq 1 ] && [[ "$1" =~ ^https?:// ]] || usage
        command -v curl >/dev/null 2>&1 || { echo "waitfor: curl is required for http" >&2; exit 2; }
        probe=probe_http
        ;;
    file) [ $# -eq 1 ] || usage; probe=probe_file ;;
    cmd) probe=probe_cmd ;;
    *) usage ;;
esac

say() { [ "$quiet" -eq 1 ] || echo "waitfor: $*" >&2; }

target="$kind $*"
start="$(date +%s)"
deadline=$((start + ${timeout_s%.*}))
wait_now="$interval"
attempt=0

say "waiting for $target (timeout ${timeout_s}s)"
while true; do
    attempt=$((attempt + 1))
    if "$probe" "$@"; then
        say "ready after $(($(date +%s) - start))s ($attempt attempt(s))"
        exit 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        say "timed out after ${timeout_s}s waiting for $target"
        exit 1
    fi
    sleep "$wait_now"
    if [ "$backoff" -eq 1 ]; then
        wait_now="$(awk -v w="$wait_now" -v m="$max_wait" 'BEGIN { w *= 2; if (w > m) w = m; print w }')"
    fi
done
