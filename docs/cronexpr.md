# cronexpr.py

Parses a five-field cron expression and prints when it next runs. Pure stdlib.

## Why

I read cron lines all the time and get the quirks wrong: does `*/2` on the
weekday field include Sunday twice? What does `0 0 13 * fri` mean? Is `7` a
valid weekday? Writing the matcher forced me to settle each of those against
how Vixie cron actually behaves, and having `--from` makes it easy to ask "what
would this do next Tuesday?" without waiting.

## API

```python
from cronexpr import CronExpr, CronError, next_runs
from datetime import datetime

e = CronExpr("*/20 9-10 * * mon-fri")
e.next_after(datetime(2026, 11, 20, 10, 30))   # datetime(2026, 11, 20, 10, 40)
e.next_runs(datetime(2026, 11, 20, 10, 30), 3) # three datetimes, in order
next_runs("@daily", datetime(2026, 11, 20), 2)  # shortcut for the above
CronExpr("61 * * * *")                          # raises CronError: minute: 61 is outside 0-59
```

`next_after` returns the first matching minute strictly after the start (the
seconds of the start are ignored), or `None` if nothing matches in the next 8
years. Times are naive: no time zones, no daylight saving. A non-string
expression raises `TypeError`.

Syntax per field: `*`, a number, `a-b`, `*/n`, `a-b/n`, `a/n`, or a comma list
of these. Months and weekdays also take three-letter names in any case.
Weekdays are `0-7` (both `0` and `7` are Sunday). `@hourly @daily @midnight
@weekly @monthly @yearly @annually` are accepted.

## CLI usage

```
cronexpr.py EXPRESSION [-n COUNT] [--from WHEN]

  -n, --count   how many times to print (default 5)
  --from WHEN   start after this time: YYYY-MM-DD, "YYYY-MM-DD HH:MM" or
                YYYY-MM-DDTHH:MM (default: now)
```

## Real example

```
$ python scripts/cronexpr.py "*/20 9-10 * * mon-fri" --from "2026-11-20 10:30" -n 4
2026-11-20 10:40 Fri
2026-11-23 09:00 Mon
2026-11-23 09:20 Mon
2026-11-23 09:40 Mon

$ python scripts/cronexpr.py "0 0 29 2 *" --from 2026-11-20 -n 2
2028-02-29 00:00 Tue
2032-02-29 00:00 Sun

$ python scripts/cronexpr.py "0 0 30 2 *"; echo "exit $?"
cronexpr: never runs in the next 8 years
exit 1
```

## Exit codes

- `0` - success.
- `1` - a bad expression, or one that never runs.
- `2` - usage error (bad `-n`, unparseable `--from`, missing expression).

## Design notes

- **Jump, don't tick.** The search starts one minute after the start and
  moves the coarsest mismatching field forward: wrong month goes to the 1st of
  the next one, wrong day to midnight tomorrow, wrong hour to the next hour,
  wrong minute to the next minute. A rare date like a leap day takes a few
  hundred steps, not four million.
- **The OR rule.** When day-of-month and day-of-week are both restricted (not
  a bare `*`), a day matches if *either* does, which is what Vixie cron does
  and what surprises people: `0 0 13 * fri` is every Friday plus every 13th.
  If either is `*`, the other decides alone. A field like `*/2` counts as
  restricted, as in cron.
- **Weekdays.** Python numbers Monday as 0 and cron numbers Sunday as 0, so the
  matcher converts. `7` is folded into `0` at parse time, and `*/n` or `a/n`
  on the weekday field stops at 6 so Sunday isn't counted twice.
- **Strict input.** Backwards ranges, zero steps, empty list items, and
  out-of-range numbers are errors that name the field. Digits must be ASCII;
  a fullwidth `１` is rejected rather than quietly accepted by `int()`.
- **Never-matching expressions** such as `0 0 30 2 *` are legal syntax. The
  search gives up after 8 years (enough to cross a leap day) and returns
  `None`.

## Running the tests

```
python tests/test_cronexpr.py
```

28 tests: parsing (lists, ranges, steps, names, Sunday as 0 or 7, shortcuts,
20 malformed expressions), rollovers across hour/day/month/year, the OR rule,
leap days, short months, impossible dates, and 60 random expressions compared
with a minute-by-minute search written straight from the definition (about ten
seconds of the run). The CLI tests cover output and all three exit codes.
