#!/usr/bin/env python3
"""Parse a five-field cron expression and say when it next runs.

Fields are minute, hour, day of month, month and day of week. Each field may be
`*`, a number, a range `a-b`, a step `*/n` or `a-b/n` or `a/n`, or a comma list
of those; months and weekdays also take three-letter names (jan..dec, sun..sat).
Day of week accepts 0-7 with both 0 and 7 meaning Sunday. The usual @-shortcuts
(@hourly, @daily, @midnight, @weekly, @monthly, @yearly, @annually) work too.

As in Vixie cron, when both day-of-month and day-of-week are restricted a day
matches if EITHER does; if one of them is `*` only the other counts. Times are
naive: no time zones and no daylight saving, so you give it local wall-clock
times and get them back. Pure stdlib."""

import argparse
import sys
from datetime import datetime, timedelta

SHORTCUTS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

# name, low, high, names (value -> offset)
FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, {n: i + 1 for i, n in enumerate(MONTHS)}),
    ("day of week", 0, 7, {n: i for i, n in enumerate(DAYS)}),
]
SEARCH_YEARS = 8  # long enough to cross a leap day; "0 0 30 2 *" never matches


class CronError(ValueError):
    pass


def _number(text, field, names):
    name, lo, hi, _ = field
    lowered = text.lower()
    if lowered in names:
        return names[lowered]
    if not text.isascii() or not text.isdigit():
        raise CronError(f"{name}: {text!r} is not a number or a name")
    return int(text)


def _parse_field(text, field):
    name, lo, hi, names = field
    values = set()
    if text == "":
        raise CronError(f"{name}: empty field")
    for part in text.split(","):
        if part == "":
            raise CronError(f"{name}: empty item in list {text!r}")
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not step_text.isascii() or not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{name}: bad step in {part!r}")
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
            if name == "day of week":
                end = 6  # */n over weekdays means 0-6; 7 is only an alias
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _number(a, field, names), _number(b, field, names)
            if start > end:
                raise CronError(f"{name}: range {base!r} runs backwards")
        else:
            start = _number(base, field, names)
            end = hi if slash else start
            if slash and name == "day of week":
                end = 6
        for v in (start, end):
            if not lo <= v <= hi:
                raise CronError(f"{name}: {v} is outside {lo}-{hi}")
        values.update(range(start, end + 1, step))
    if name == "day of week" and 7 in values:
        values.discard(7)
        values.add(0)
    return frozenset(values)


class CronExpr:
    """A parsed expression. `restricted_dom` and `restricted_dow` remember
    whether the field was anything other than a bare `*`, for the OR rule."""

    def __init__(self, text):
        if not isinstance(text, str):
            raise TypeError("expression must be a string")
        source = text.strip()
        source = SHORTCUTS.get(source.lower(), source)
        if source.startswith("@"):
            raise CronError(f"unknown shortcut {source!r}")
        parts = source.split()
        if len(parts) != 5:
            raise CronError(f"expected 5 fields, got {len(parts)}")
        self.minutes, self.hours, self.doms, self.months, self.dows = (
            _parse_field(p, f) for p, f in zip(parts, FIELDS)
        )
        self.restricted_dom = parts[2] != "*"
        self.restricted_dow = parts[4] != "*"

    def day_matches(self, when):
        dom = when.day in self.doms
        dow = (when.weekday() + 1) % 7 in self.dows  # Monday=0 in Python, Sunday=0 in cron
        if self.restricted_dom and self.restricted_dow:
            return dom or dow
        return dom and dow

    def next_after(self, start):
        """The first matching minute strictly after `start`, or None if there is
        none in the next SEARCH_YEARS years."""
        t = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = start.replace(year=min(start.year + SEARCH_YEARS, 9999 - 1))
        while t <= limit:
            if t.month not in self.months:
                t = _first_of_next_month(t)
            elif not self.day_matches(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        return None

    def next_runs(self, start, count):
        out = []
        t = start
        while len(out) < count:
            t = self.next_after(t)
            if t is None:
                break
            out.append(t)
        return out


def _first_of_next_month(t):
    if t.month == 12:
        return t.replace(year=t.year + 1, month=1, day=1, hour=0, minute=0)
    return t.replace(month=t.month + 1, day=1, hour=0, minute=0)


def next_runs(expr, start, count=5):
    """Convenience: the next `count` run times of `expr` after `start`."""
    return CronExpr(expr).next_runs(start, count)


def _parse_when(text):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"{text!r} is not YYYY-MM-DD[ HH:MM]")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cronexpr.py",
        description="Print the next run times of a five-field cron expression.",
        epilog="Exit 0 on success, 1 for a bad expression or one that never runs, 2 for a usage error.",
    )
    ap.add_argument("expression", help='e.g. "*/15 9-17 * * mon-fri" (quote it)')
    ap.add_argument("-n", "--count", type=int, default=5, help="how many times to print (default 5)")
    ap.add_argument("--from", dest="start", type=_parse_when, help="start after this time (default: now)")
    args = ap.parse_args(argv)
    if args.count < 1:
        ap.error("-n must be at least 1")
    try:
        expr = CronExpr(args.expression)
    except CronError as err:
        print(f"cronexpr: {err}", file=sys.stderr)
        return 1
    start = args.start or datetime.now()
    runs = expr.next_runs(start, args.count)
    if not runs:
        print(f"cronexpr: never runs in the next {SEARCH_YEARS} years", file=sys.stderr)
        return 1
    for r in runs:
        print(r.strftime("%Y-%m-%d %H:%M %a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
