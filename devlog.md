# Devlog

Notes on what I actually worked on, in the order I did it. New entries go on top.

## 2026-11-11

Added `scripts/uritemplate.py` - expands RFC 6570 URI templates
(`/repos/{owner}/{repo}/issues{/number}{?state}`). Deploy scripts and API
clients build URLs all day, and f-strings are how `?q=a b` or
`/issues/None` ends up in a request.

Decisions I want to remember:

- One table drives all eight operators, as in the RFC's Appendix A.
  `{?x}` gives `?x=` for an empty string and `{;x}` gives `;x`; the only
  difference is one cell in that table.
- `{+x}` and `{#x}` keep reserved characters and existing `%XX` triplets,
  but a lone `%` is still `%25`.
- Prefixes count characters, not bytes, and in `{+x:N}` a triplet counts as
  one character so it's never cut in half. A prefix on a list is an error
  rather than something to quietly ignore.
- `None`, `[]` and `{}` are undefined and disappear with their prefix;
  `""` is defined. Booleans are refused because every API spells them
  differently.
- Malformed templates raise with a position instead of returning half a URL.

Testing: the main test runs every example in RFC 6570 sections 3.2.1 to
3.2.9. My first copy of the tables was short by four rows (the
`O{empty}X` ones). The row-count assert failed, though only because I'd
guessed the count wrong too. Extracting the rows from the RFC text and
diffing them against the test showed what was missing, and the copy now
matches row for row. The script passed all of them. I broke it five ways (wrong
`ifemp` for `;`, prefixes splitting triplets, empty lists counting as
defined, unencoded dict keys, accepting future operators), and each made 1
to 5 tests fail.

## 2026-11-09

Added `scripts/ignorematch.py` - works out what a project's `.gitignore`
files ignore, without git. Backup scripts, packagers and watchers all need
to skip ignored files, and a quick `fnmatch` gets the rules wrong.

Decisions I want to remember:

- The rule people forget: nothing inside an ignored directory can be
  re-included. With `build/` ignored, `!build/keep.txt` does nothing, but
  `build/*` plus `!build/keep.txt` works. `why()` checks every parent
  directory before the path itself.
- A slash at the start or in the middle anchors a pattern to its
  `.gitignore`'s directory. A trailing slash alone doesn't anchor; it means
  "directories only".
- `**` is only special as a whole segment: zero or more directories in `**/x`
  and `a/**/b`, one or more in `a/**`. In `a**b` it's just `*`.
- Deeper `.gitignore` files are added after shallower ones, so they win;
  `.git/info/exclude` goes first, so it loses to everything.
- The real check is a test that builds a git repository and compares my
  answers with `git check-ignore` for 8 pattern sets over 30 paths. They
  agreed on the first run, and so did the other 56 tests, so I broke the
  script four ways (no parent check, `*` crossing `/`, no anchoring,
  ignoring the directory-only flag). Each failed 3 to 7 tests, and the git
  cross-check caught all four on its own.

## 2026-11-06

Added `scripts/applypatch.py` - applies a unified diff to a tree of files,
all or nothing. `patch` and `git apply` aren't always there, and I wanted
two guarantees together: nothing changes unless every hunk fits, and a diff
from elsewhere can't write outside the directory it's applied to.

What the tests caught on the first run (four things in the script):

- A `\ No newline at end of file` marker in the *middle* of a hunk (after
  the last `-` line, before the `+` lines) was rejected as garbage. I'd only
  handled it at the end of a hunk. Parsing now treats it as belonging to the
  line above wherever it appears.
- Path traversal got through with `-p1`: `+++ ../x` lost its `..` to the
  strip before I checked for it. The check now runs on the whole path first.
- The progress lines printed `new\dir\f.txt` on Windows. Paths are now
  joined with `/` internally.
- The offset reported for a later hunk was relative to the previous hunk, not
  to the diff. It's now measured against the diff, like GNU patch.

One more failure was a mistake in my own test (a wrong strip count).

Decisions I want to remember:

- Context must match exactly but position needn't: each hunk is tried at the
  stated line, then growing offsets either side, nearest wins, and no fuzz. A
  hunk whose context changed fails instead of landing somewhere doubtful.
- Final newlines are part of matching. A hunk that mentions a missing final
  newline only matches at the end of a file that really lacks one.
- Everything is worked out in memory before anything is written, so a failed
  hunk never leaves a half-patched tree. A full disk during the write phase
  is the one case I can't promise about.
- `-R` swaps the two sides of every hunk and the two paths, so reversing a
  creation deletes the file.
- A seeded random test pushes `difflib` diffs of random edits through both
  directions. I also broke the script three ways (no `..` check, ignoring
  the final-newline rule, no ordering floor) and each failed 2 to 4 tests.

## 2026-11-04

Added `scripts/mdtable.py` - tidies Markdown tables so the columns line up.
Add one longer cell and every other row in the source is out of line; the
rendered page looks fine but diffs are harder to read. It has a `--check`
mode for a pre-commit hook or CI, and `-i` to rewrite files.

Decisions I want to remember:

- What counts as a table is deliberately strict: a header, a delimiter row
  with the same number of cells, and at most three spaces of indent. Prose
  with a stray `|` is left alone, and a four-space indent (a code block) is
  not mistaken for a table. Fenced code and quoted tables are skipped.
- Widths are display widths, not `len()`. Japanese text counts 2 columns and
  combining accents count 0, otherwise `日本語` never lines up.
- `\|` is content, not a separator, and counts toward the cell's width.
- A row with more cells than the header is an error, not something to drop.
  Some renderers throw the extra cells away; I'd rather be told.
- A line with no pipe ends the table. Renderers disagree about that case,
  and ending the table is the cautious reading.
- In-place writes go through a temporary file and `os.replace`, so an
  interrupted run can't leave a half-written README, and a file that's
  already tidy isn't touched at all.
- Line endings are preserved (CRLF stays CRLF, no final newline stays none),
  which means reading and writing without newline translation.

All 67 tests passed on the first run, which made me suspicious, so I
broke the script on purpose twice (removed the fence check, then the
indent check) to confirm the matching tests fail. They did.

Added `tests/test_mdtable.py` (`unittest`, 67 tests, one POSIX-only skipped
on Windows) and `docs/mdtable.md`.

## 2026-11-03

Added `scripts/jsonl.py` - filter, pick fields from and count JSON Lines.
`grep 500` on a structured log matches a timestamp as happily as a status,
and what I actually want is "records where `status` is at least 500" or
"how many per path". `jq` does it but isn't installed on every machine I
use, and I only need three of its jobs.

Decisions I want to remember:

- A missing field is not `null`. `get_path` returns a marker, so
  `status=null` matches only a real null, and a missing field makes `!=`
  true and every ordering operator false.
- JSON equality, not Python equality. In Python `True == 1`; in JSON they
  are different, so `a=1` must not match `true`. Same for `200` against
  `"200"`.
- Ordering only compares two numbers or two strings. `"10" > 5` is false
  instead of an exception, so one odd record doesn't kill a long run.
- It streams, which means a bad line half way through is reported after the
  earlier records have been printed. `--skip-bad` is the escape hatch.

The tests caught a real bug: output containing `café` came back undecodable
because a Windows pipe uses the locale code page (cp1252), not UTF-8, and a
character outside that code page would have crashed the printer outright.
JSON Lines is UTF-8, so both streams are now forced to it, and there's a
test with Japanese text and an emoji so it can't quietly regress.

Also: a closed pipe (`| head`) is `EPIPE` on Unix but `EINVAL` on Windows,
and my first closed-pipe test deadlocked because it wrote all its input
before reading any output. Feeding the input from a file fixed it.

Added `tests/test_jsonl.py` (`unittest`, 69 tests) and `docs/jsonl.md`.

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
