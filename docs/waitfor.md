# waitfor.sh

Waits until something is ready, then exits: a TCP port accepts
connections, an HTTP URL answers, a file appears, or a command succeeds.
For deploy and startup scripts. Polls with an optional exponential backoff
and gives up after a timeout. Needs bash and coreutils (`http` also needs
`curl`).

## Why

"Don't run the migrations until the database is up" and "don't smoke-test
until the new container answers" come up in every deploy. The usual answer
is `sleep 10` and hope, which is either too short and flaky, or too long and
wastes time on every run. This polls instead: it returns the moment the
thing is ready and fails loudly with a non-zero exit if it never is.

## Usage

```
waitfor.sh [options] KIND TARGET...

  tcp HOST:PORT      port accepts a connection
  http URL           URL answers (2xx/3xx, or exactly --status)
  file PATH          path exists
  cmd COMMAND ARGS   command exits 0

  -t SECONDS   give up after this long (default 30; 0 = try once)
  -i SECONDS   wait between attempts (default 1)
  -b           back off: double the wait after each failure
  -m SECONDS   longest wait when backing off (default 5)
  -s CODE      with http: require exactly this status code
  -q           no output
```

## Real example

```
$ waitfor.sh -t 60 tcp db.internal:5432 && ./migrate.sh
waitfor: waiting for tcp db.internal:5432 (timeout 60s)
waitfor: ready after 7s (8 attempt(s))

$ waitfor.sh -t 5 -b -m 2 http http://localhost:8080/health
waitfor: waiting for http http://localhost:8080/health (timeout 5s)
waitfor: timed out after 5s waiting for http http://localhost:8080/health
$ echo $?
1

$ waitfor.sh -s 401 http https://api.example.com/private   # up, but needs auth
$ waitfor.sh cmd pg_isready -h db.internal                   # anything with an exit code
```

Progress goes to stderr, so it never pollutes output you're capturing.

## Exit codes

- `0` - ready.
- `1` - timed out.
- `2` - bad usage (unknown option, missing target, a non-numeric value,
  `http` without `curl`).

## Design notes

- **Arguments are never put into a command string.** `cmd` runs
  `"$@"` directly, and `tcp` hands the host and port to a child `bash` as
  positional parameters. A target like `a b; rm -rf x` is just an odd
  filename, not a command. A test checks this.
- **`http` only accepts `http://` and `https://` URLs**, so a target
  beginning with `-` can never be read by `curl` as an option.
- **Every probe is bounded.** A `tcp` attempt is capped at 2 seconds and an
  `http` attempt at 3, so one hung connection can't swallow the whole
  timeout.
- **Default "ready" for `http` is 2xx or 3xx.** A 404 or 503 means "not
  yet" (the usual state while a service starts). `-s` is for the case where
  a non-2xx is the healthy answer.
- **The timeout is checked in whole seconds**, so it can end up to a
  second early or late. That's fine for deploy waits and keeps it to
  `date +%s`, which works in any bash.
- **Backoff is capped.** With `-b` the delay doubles (1, 2, 4...) up to
  `-m`, so a slow start doesn't get hammered, but you also never wait
  longer than the cap to notice it came up.
- **`-t 0` means check once.** Handy as a plain "is it up right now" test.

## Running the tests

```
bash tests/test_waitfor.sh
```

36 assertions, about 20 seconds: immediate readiness, timeout, `-t 0`, `-q`,
a file that appears one second in (and the wait neither returning early nor
running to the timeout), `cmd` success and failure, arguments passed as
words and shell metacharacters not executed, a command that passes on the
third attempt, backoff making fewer attempts than a fixed interval, a closed
and an open port, a 200, a 404 (not ready, then accepted with `-s 404`), a
wrong `-s`, a stopped server, and eleven different usage errors. The `tcp` and
`http` cases use a throwaway `python -m http.server` and are skipped if
`python` or `curl` is missing.
