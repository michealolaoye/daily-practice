# Devlog

Notes on what I actually worked on, in the order I did it. New entries go on top.

## 2026-11-02

Added `scripts/toposort.py` - orders things so each one comes after what it
depends on, either as a flat list or as levels that could run in parallel.
Build steps, migrations and start-up order all have this shape. What I
wanted was a useful error: "there's a cycle somewhere" is no help in a graph
of 200 items, `a -> b -> c -> a` is.

Decisions I want to remember:

- Finding the cycle is easy once you see why: when the sort gets stuck,
  every leftover item still depends on another leftover item. Follow any one
  dependency and you must eventually repeat an item, and that loop is a
  real cycle. Items that only lead into it, or hang off it, aren't reported.
- No recursion. It works level by level, so a chain of 5000 items is fine.
  There's a test for exactly that.
- Ties keep first-mention order so the output is stable, which matters when
  you diff build logs.
- A self-dependency (`x: x`) is a cycle, not something to skip.
- The CLI builds the whole answer before printing, so a cycle never leaves
  half an order on stdout for the next script to act on.

Added `tests/test_toposort.py` (`unittest`, 45 tests) and
`docs/toposort.md`.

## 2026-10-30

Added `scripts/waitfor.sh` - waits until a TCP port, an HTTP URL, a file or
a command is ready, then exits. It's the deploy-side tool I keep reaching
for: "don't run migrations until the database is up". `sleep 10` is the
usual answer and it's always either too short or too long, so this polls
and returns the moment the thing is ready, or exits 1 if it never is.

Choices:

- Arguments are never built into a command string. `cmd` runs `"$@"`
  directly and `tcp` passes host and port to a child `bash` as positional
  parameters, so a target with spaces or a `;` is just a strange name. I
  wrote a test with a filename containing `; touch ...` and checked the
  file never appears.
- Every probe has its own cap (2s for tcp, 3s for http), otherwise one hung
  connection can eat the entire timeout.
- For http, "ready" is 2xx or 3xx. A 404 or 503 is the normal state while a
  service starts, so it counts as not yet; `-s` covers the case where an
  odd status is the healthy answer.

The timeout uses whole seconds from `date +%s`, so it can be a second off.
That's fine here and it works in any bash, which I preferred to chasing
sub-second timing.

Also added a `.gitattributes` so `*.sh` files always keep LF line endings.
This machine converts to CRLF on checkout, and a CRLF shell script fails
with a confusing error.

Added `tests/test_waitfor.sh` (36 assertions; the tcp/http cases use a
throwaway `python -m http.server`) and `docs/waitfor.md`.

## 2026-10-29

Started this repo and added `scripts/ttlcache.py`, an in-memory cache where
entries expire after a time-to-live, with an optional size cap. I reach for
"remember this for a minute" more often than "remember the last N things",
and an LRU cache can't do the first one because it only evicts on size.

Decisions I want to remember:

- Expiry is lazy. No background thread; dead entries are dropped when
  something looks at them, plus a `purge()` for when memory matters.
- Reading an entry doesn't extend it, so it's a real TTL and not an idle
  timeout. Writing the key again restarts the clock.
- When the cache is full I drop expired entries before evicting any live
  one, so a live entry only goes if there's no dead weight to remove.
- The clock is a parameter, defaulting to `time.monotonic`. That made every
  expiry test exact and sleep-free, and it avoids wall-clock jumps.

Added `tests/test_ttlcache.py` (`unittest`, 29 tests) and `docs/ttlcache.md`.
