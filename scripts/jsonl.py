#!/usr/bin/env python3
"""Filter, pick fields from and count JSON Lines (one JSON value per line).

A small `grep`/`cut`/`uniq -c` for log files and API dumps that are written
one JSON object per line. Streams its input, so it works on files much bigger
than memory. Fields are addressed with dotted paths ("user.name",
"items.0.id"). Pure stdlib."""

import argparse
import errno
import json
import os
import re
import sys
from collections import Counter

MISSING = object()

_CONDITION = re.compile(r"^([^=!<>]+?)(!=|>=|<=|=|>|<)(.*)$")


def get_path(value, path):
    """Follow a dotted path into nested dicts and lists. Digits index a list
    ("items.0"). Returns MISSING if any step is absent, so a real null is
    not confused with a field that is not there."""
    for part in path.split("."):
        if isinstance(value, dict):
            if part not in value:
                return MISSING
            value = value[part]
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            if index >= len(value):
                return MISSING
            value = value[index]
        else:
            return MISSING
    return value


def parse_condition(text):
    """Turn `status>=500` into (path, op, value). The value is read as JSON
    when it parses (500, true, null, "x") and is otherwise the plain text,
    so `name=bob` works without quoting. Raises ValueError."""
    match = _CONDITION.match(text)
    if not match or not match.group(1).strip():
        raise ValueError(f"not a condition (expected PATH=VALUE, PATH>=VALUE, ...): {text!r}")
    path, op, raw = match.group(1).strip(), match.group(2), match.group(3)
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    return path, op, value


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _equal(a, b):
    # In Python True == 1; in JSON they are different values.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def matches(record, condition):
    """True if `record` satisfies `condition`. A missing field is not equal
    to anything, so `!=` is true for it and every other operator is false.
    Ordering only compares two numbers or two strings; mixed types are false
    rather than an error."""
    path, op, wanted = condition
    found = get_path(record, path)
    if op == "=":
        return found is not MISSING and _equal(found, wanted)
    if op == "!=":
        return found is MISSING or not _equal(found, wanted)
    if found is MISSING:
        return False
    both_numbers = _is_number(found) and _is_number(wanted)
    both_strings = isinstance(found, str) and isinstance(wanted, str)
    if not (both_numbers or both_strings):
        return False
    return {"<": found < wanted, "<=": found <= wanted,
            ">": found > wanted, ">=": found >= wanted}[op]


def select(record, paths):
    """A new object holding just `paths`, keyed by the path as written.
    A missing field becomes null so every output line has the same keys."""
    out = {}
    for path in paths:
        found = get_path(record, path)
        out[path] = None if found is MISSING else found
    return out


def records(lines, skip_bad=False, on_bad=None):
    """Yield (line_number, value) for each non-blank line. A line that is not
    valid JSON raises ValueError naming the line, or, with `skip_bad`, is
    reported to `on_bad(line_number)` and skipped."""
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            yield number, json.loads(line)
        except ValueError as err:
            if not skip_bad:
                raise ValueError(f"line {number}: not valid JSON ({err})") from None
            if on_bad is not None:
                on_bad(number)


def _writing_failed(err):
    """True if `err` came from writing to stdout rather than reading the
    input. Reading errors carry the input's filename; a failed write does
    not."""
    return err.filename is None


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def count_by(items, path):
    """Count records by the value at `path`, most common first (ties in the
    order first seen). Records without the field are counted under null,
    and a value that is a list or object is counted by its JSON text."""
    counts = Counter()
    for record in items:
        found = get_path(record, path)
        key = None if found is MISSING else found
        counts[dumps(key)] += 1
    return [(json.loads(k), n) for k, n in counts.most_common()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", nargs="?", default="-", help="JSON Lines file (default: stdin)")
    parser.add_argument("-w", "--where", action="append", default=[], metavar="COND",
                        help="keep records matching PATH=VALUE (also !=, >, >=, <, <=); repeat to require all")
    parser.add_argument("-s", "--select", metavar="PATHS", help="comma-separated paths to keep")
    parser.add_argument("-c", "--count-by", metavar="PATH", help="count records by this path instead of printing them")
    parser.add_argument("-n", "--limit", type=int, metavar="N", help="stop after N matching records")
    parser.add_argument("--skip-bad", action="store_true", help="skip lines that are not valid JSON (and say how many)")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 0:
        parser.error("-n must not be negative")
    try:
        conditions = [parse_condition(c) for c in args.where]
    except ValueError as err:
        parser.error(str(err))
    paths = [p.strip() for p in args.select.split(",")] if args.select else None
    if paths is not None and not all(paths):
        parser.error("-s has an empty path")
    if args.count_by is not None and (paths is not None or args.limit is not None):
        parser.error("-c cannot be combined with -s or -n")

    bad = []
    try:
        stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    except OSError as err:
        print(f"jsonl: {err}", file=sys.stderr)
        return 2
    try:
        with stream:
            kept = (r for _, r in records(stream, args.skip_bad, bad.append)
                    if all(matches(r, c) for c in conditions))
            if args.count_by is not None:
                for value, n in count_by(kept, args.count_by):
                    print(dumps({"value": value, "count": n}))
            else:
                emitted = 0
                for record in kept:
                    if args.limit is not None and emitted >= args.limit:
                        break
                    print(dumps(select(record, paths) if paths else record))
                    emitted += 1
            sys.stdout.flush()
    except OSError as err:
        # Downstream (e.g. `| head`) stopped reading. That is EPIPE on Unix
        # but EINVAL on Windows, and it is not an error. Point stdout at
        # devnull so the interpreter does not complain again when it flushes
        # during shutdown.
        if err.errno not in (errno.EPIPE, errno.EINVAL) or not _writing_failed(err):
            print(f"jsonl: {err}", file=sys.stderr)
            return 2
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
        return 0
    except (ValueError, UnicodeDecodeError) as err:
        print(f"jsonl: {err}", file=sys.stderr)
        return 1

    if bad:
        shown = ", ".join(str(n) for n in bad[:5]) + (", ..." if len(bad) > 5 else "")
        print(f"jsonl: skipped {len(bad)} line(s) that were not valid JSON (line {shown})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
