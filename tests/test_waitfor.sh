#!/usr/bin/env bash
# Assertion-based tests for waitfor.sh. No test framework dependency.
# The tcp/http cases use a throwaway python http.server and are skipped if
# python isn't available.
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$script_dir/../scripts/waitfor.sh"

pass=0
fail=0
skipped=0

assert_eq() {
    local actual="$1" expected="$2" label="$3"
    if [ "$actual" = "$expected" ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $label - expected '$expected', got '$actual'"
    fi
}

assert_contains() {
    local haystack="$1" needle="$2" label="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $label - expected to find '$needle' in: $haystack"
    fi
}

assert_true() {
    local label="$1"
    shift
    if "$@"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $label"
    fi
}

tmpdir="$(mktemp -d)"
server_pid=""
cleanup() {
    [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null
    rm -rf "$tmpdir"
}
trap cleanup EXIT

run() { bash "$script" "$@" 2>&1; }

# --- file -------------------------------------------------------------
touch "$tmpdir/exists"
out="$(run file "$tmpdir/exists")"
assert_eq "$?" "0" "existing file is ready at once"
assert_contains "$out" "ready after 0s (1 attempt(s))" "reports a single attempt"

out="$(run -t 1 -i 0.2 file "$tmpdir/never")"
assert_eq "$?" "1" "missing file times out with exit 1"
assert_contains "$out" "timed out" "timeout message"

run -t 0 file "$tmpdir/never" >/dev/null
assert_eq "$?" "1" "-t 0 tries exactly once and fails"

out="$(run -q file "$tmpdir/exists")"
assert_eq "$out" "" "-q prints nothing on success"
out="$(run -q -t 0 file "$tmpdir/never")"
assert_eq "$out" "" "-q prints nothing on timeout"

# A file that shows up partway through the wait.
(sleep 1; touch "$tmpdir/late") &
start=$SECONDS
out="$(run -t 10 -i 0.2 file "$tmpdir/late")"
assert_eq "$?" "0" "file that appears later is picked up"
assert_true "waited for it rather than returning early" [ $((SECONDS - start)) -ge 1 ]
assert_true "did not wait for the whole timeout" [ $((SECONDS - start)) -lt 8 ]

# --- cmd --------------------------------------------------------------
run cmd true >/dev/null
assert_eq "$?" "0" "successful command is ready"
run -t 0 cmd false >/dev/null
assert_eq "$?" "1" "failing command times out"

# The command gets its arguments as separate words, not a shell string.
run cmd test -d "$tmpdir" >/dev/null
assert_eq "$?" "0" "arguments are passed through"
weird="$tmpdir/a b; touch $tmpdir/injected"
run -t 0 cmd test -e "$weird" >/dev/null
assert_true "shell metacharacters in arguments are not executed" [ ! -e "$tmpdir/injected" ]

# Becomes ready on the third attempt.
counter="$tmpdir/count"
: > "$counter"
cat > "$tmpdir/third.sh" <<EOF
#!/usr/bin/env bash
echo x >> "$counter"
[ "\$(wc -l < "$counter")" -ge 3 ]
EOF
out="$(run -t 10 -i 0.1 cmd bash "$tmpdir/third.sh")"
assert_eq "$?" "0" "command that passes on attempt 3 succeeds"
assert_contains "$out" "3 attempt(s)" "reports 3 attempts"

# --- backoff ----------------------------------------------------------
count_attempts() {
    : > "$counter"
    bash "$script" -q -t 3 "$@" cmd bash -c "echo x >> '$counter'; false" >/dev/null 2>&1
    wc -l < "$counter" | tr -d ' '
}
plain="$(count_attempts -i 0.1)"
backed="$(count_attempts -i 0.1 -b -m 1)"
assert_true "backoff makes fewer attempts than a fixed interval ($backed < $plain)" [ "$backed" -lt "$plain" ]
assert_true "backoff still tries more than once" [ "$backed" -ge 2 ]

# --- tcp / http -------------------------------------------------------
if command -v python >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    port="$(python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
    run -t 1 -i 0.2 tcp "127.0.0.1:$port" >/dev/null
    assert_eq "$?" "1" "closed port times out"

    echo hello > "$tmpdir/index.html"
    (cd "$tmpdir" && exec python -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1) &
    server_pid=$!

    run -t 10 -i 0.2 tcp "127.0.0.1:$port" >/dev/null
    assert_eq "$?" "0" "open port is ready"
    run -t 10 -i 0.2 http "http://127.0.0.1:$port/" >/dev/null
    assert_eq "$?" "0" "200 response is ready"
    run -t 1 -i 0.2 http "http://127.0.0.1:$port/missing" >/dev/null
    assert_eq "$?" "1" "404 is not ready by default"
    run -t 5 -i 0.2 -s 404 http "http://127.0.0.1:$port/missing" >/dev/null
    assert_eq "$?" "0" "-s 404 accepts a 404"
    run -t 1 -i 0.2 -s 201 http "http://127.0.0.1:$port/" >/dev/null
    assert_eq "$?" "1" "-s requires that exact status"

    kill "$server_pid" 2>/dev/null
    wait "$server_pid" 2>/dev/null
    server_pid=""
    run -t 1 -i 0.2 http "http://127.0.0.1:$port/" >/dev/null
    assert_eq "$?" "1" "stopped server is not ready"
else
    skipped=1
    echo "SKIP: tcp/http tests need python and curl"
fi

# --- usage errors -----------------------------------------------------
for args in "" "file" "bogus x" "-t" "-t abc file x" "-i -1 file x" "-s 20 http http://x" \
            "tcp nohostport" "tcp host:port" "http ftp://x" "--bogus file x"; do
    # shellcheck disable=SC2086
    bash "$script" $args >/dev/null 2>&1
    assert_eq "$?" "2" "usage error for: '$args'"
done

echo "$pass passed, $fail failed$([ "$skipped" -eq 1 ] && echo ', tcp/http skipped')"
[ "$fail" -eq 0 ]
