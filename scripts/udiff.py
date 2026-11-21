#!/usr/bin/env python3
"""Write a unified diff of two files, using Myers' O(ND) algorithm.

This is the other half of applypatch.py: it finds a shortest edit script
between two lists of lines (Eugene Myers, "An O(ND) Difference Algorithm and
Its Variations", 1986) and prints it the way `diff -u` does, with the same
hunk headers, context merging and "\\ No newline at end of file" marker, so
`patch` and `git apply` accept the result. Lines are compared exactly, line
ending included. If two files differ so much that the search would need too
much memory, the diff falls back to replacing the whole file, which is still
a correct patch, just not a minimal one. Pure stdlib."""

import argparse
import sys

NO_NEWLINE = "\\ No newline at end of file\n"
DEFAULT_MAX_EDITS = 4000  # the search keeps O(D^2) memory; beyond this, replace all


def split_lines(text):
    """Split on \\n only, keeping each line's ending (so \\r\\n stays intact)."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def edit_script(a, b, max_edits=DEFAULT_MAX_EDITS):
    """Return a list of ("=" | "-" | "+", a_index, b_index) for the shortest
    edit script turning a into b. For "-" b_index is the position in b where
    the line would have been; for "+" a_index likewise. Returns (ops, exact):
    exact is False when the fallback replace-all was used."""
    # Common prefix and suffix are free, and usually most of the file.
    n, m = len(a), len(b)
    start = 0
    while start < n and start < m and a[start] == b[start]:
        start += 1
    end = 0
    while end < n - start and end < m - start and a[n - 1 - end] == b[m - 1 - end]:
        end += 1
    core_a, core_b = a[start : n - end], b[start : m - end]
    middle, exact = _myers(core_a, core_b, max_edits)
    ops = [("=", i, i) for i in range(start)]
    for tag, i, j in middle:
        ops.append((tag, i + start, j + start))
    ops += [("=", n - end + k, m - end + k) for k in range(end)]
    return ops, exact


def _myers(a, b, max_edits):
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return [("-", i, 0) for i in range(n)] + [("+", 0, j) for j in range(m)], True
    max_d = min(n + m, max_edits)
    v = {1: 0}
    trace = []
    found = None
    for d in range(max_d + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
                x = v[k + 1]  # step down: take a line from b
            else:
                x = v[k - 1] + 1  # step right: drop a line from a
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                found = d
                break
        if found is not None:
            break
    if found is None:
        return [("-", i, 0) for i in range(n)] + [("+", n, j) for j in range(m)], False
    ops = []
    x, y = n, m
    for d in range(found, -1, -1):
        vd = trace[d]
        k = x - y
        if k == -d or (k != d and vd.get(k - 1, -1) < vd.get(k + 1, -1)):
            pk = k + 1
        else:
            pk = k - 1
        px = vd[pk]
        py = px - pk
        while x > px and y > py:
            x -= 1
            y -= 1
            ops.append(("=", x, y))
        if d > 0:
            if x == px:
                y -= 1
                ops.append(("+", x, y))
            else:
                x -= 1
                ops.append(("-", x, y))
        x, y = px, py
    ops.reverse()
    return ops, True


def _range(start, length):
    # "diff -u" counts from 1, omits ",1", and for an empty range names the
    # line *before* the gap.
    if length == 1:
        return f"{start + 1}"
    return f"{start + 1 if length else start},{length}"


def hunks(ops, context):
    """Group an edit script into (a_start, a_len, b_start, b_len, ops) hunks."""
    changes = [i for i, op in enumerate(ops) if op[0] != "="]
    if not changes:
        return []
    groups = []
    first = last = changes[0]
    for i in changes[1:]:
        if i - last - 1 > 2 * context:
            groups.append((first, last))
            first = i
        last = i
    groups.append((first, last))
    out = []
    for first, last in groups:
        lo = max(0, first - context)
        hi = min(len(ops), last + context + 1)
        chunk = ops[lo:hi]
        a_start = next((op[1] for op in chunk if op[0] != "+"), None)
        b_start = next((op[2] for op in chunk if op[0] != "-"), None)
        a_len = sum(1 for op in chunk if op[0] != "+")
        b_len = sum(1 for op in chunk if op[0] != "-")
        # a pure insertion/deletion hunk has no lines on one side; its start
        # is where the other side's first op sits
        if a_start is None:
            a_start = chunk[0][1]
        if b_start is None:
            b_start = chunk[0][2]
        out.append((a_start, a_len, b_start, b_len, chunk))
    return out


def unified_diff(a_text, b_text, a_name="a", b_name="b", context=3, max_edits=DEFAULT_MAX_EDITS):
    """The diff as a string; empty if the texts are identical."""
    if context < 0:
        raise ValueError("context must be >= 0")
    a, b = split_lines(a_text), split_lines(b_text)
    ops, _ = edit_script(a, b, max_edits)
    parts = []
    for a_start, a_len, b_start, b_len, chunk in hunks(ops, context):
        parts.append(f"@@ -{_range(a_start, a_len)} +{_range(b_start, b_len)} @@\n")
        for tag, i, j in chunk:
            line = b[j] if tag == "+" else a[i]
            parts.append(("+" if tag == "+" else "-" if tag == "-" else " ") + line)
            if not line.endswith("\n"):
                parts.append("\n" + NO_NEWLINE)
    if not parts:
        return ""
    return f"--- {a_name}\n+++ {b_name}\n" + "".join(parts)


def _read(path):
    with open(path, "rb") as f:
        data = f.read()
    return data


def main(argv=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    p = argparse.ArgumentParser(description="Unified diff of two files (Myers).")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("-U", "--unified", type=int, default=3, metavar="N", help="lines of context (default 3)")
    p.add_argument("--max-edits", type=int, default=DEFAULT_MAX_EDITS,
                   help="past this many edits, replace the whole file instead of searching")
    args = p.parse_args(argv)
    if args.unified < 0:
        print("udiff: context must be >= 0", file=stderr)
        return 2
    try:
        da, db = _read(args.a), _read(args.b)
    except OSError as err:
        print(f"udiff: {err}", file=stderr)
        return 2
    if da == db:
        return 0
    if b"\0" in da[:8000] or b"\0" in db[:8000]:
        print(f"Binary files {args.a} and {args.b} differ", file=stdout)
        return 1
    # surrogateescape keeps any non-UTF-8 byte exactly as it was
    ta, tb = da.decode("utf-8", "surrogateescape"), db.decode("utf-8", "surrogateescape")
    text = unified_diff(ta, tb, args.a, args.b, args.unified, args.max_edits)
    out = getattr(stdout, "buffer", None)
    if out is not None:
        out.write(text.encode("utf-8", "surrogateescape"))
        out.flush()
    else:
        stdout.write(text)
    return 1


if __name__ == "__main__":
    sys.exit(main())
