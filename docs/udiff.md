# udiff.py

Writes a unified diff of two files, the way `diff -u` does, using Myers'
O(ND) algorithm. It is the other half of `applypatch.py`: this makes the
patch, that one applies it. Pure stdlib.

## Why

`difflib.unified_diff` exists, but I wanted to see how a shortest edit
script is actually found, and to produce exactly what `patch` and `git apply`
expect: the same hunk headers, the same rule for when two changes share a
hunk, and the "No newline at end of file" marker. Those details are where a
hand-rolled diff usually breaks, and the way to know they are right is to feed
the output to real tools.

## API

```python
from udiff import unified_diff, edit_script, split_lines

print(unified_diff(old_text, new_text, "a/app.py", "b/app.py", context=3), end="")

ops, exact = edit_script(split_lines(old_text), split_lines(new_text))
# ops: [("=", i, j) | ("-", i, j) | ("+", i, j), ...]; exact is False if it had to replace everything
```

`unified_diff` returns `""` when the texts are the same, and raises
`ValueError` for a negative `context`. Lines are compared exactly, line
ending included, so `a\r\n` and `a\n` are different lines, and so are `a`
and `a\n` (that is how the end-of-file marker arises).

## CLI usage

```
udiff.py [-U N] [--max-edits N] A B

  -U, --unified N   lines of context (default 3)
  --max-edits N     past this many edits, replace the whole file (default 4000)
```

Prints the diff to stdout. Like `diff`: exit `0` if the files are the
same, `1` if they differ, `2` if something went wrong.

## Real example

```
$ python scripts/udiff.py -U1 old.txt new.txt
--- old.txt
+++ new.txt
@@ -2,3 +2,3 @@
 two
-three
+3
 four
@@ -10 +10,2 @@
 ten
+eleven
\ No newline at end of file

$ python scripts/udiff.py old.txt old.txt; echo "exit $?"
exit 0

$ python scripts/udiff.py old.txt missing; echo "exit $?"
udiff: [Errno 2] No such file or directory: 'missing'
exit 2
```

The patch goes straight into the sibling tool:
`python scripts/applypatch.py -C dir p.diff` reproduces `new.txt` byte for
byte.

## Exit codes

- `0` - the files are identical.
- `1` - they differ (the diff is on stdout; for binary files, a one-line notice).
- `2` - a file could not be read, or a bad option.

## Design notes

- **Shortest script.** The search walks diagonals of the edit graph, one
  more edit at a time, keeping the furthest point reached on each diagonal. It
  stops at the first path that reaches the corner, so it is minimal in the
  number of added plus deleted lines. The tests check that against a
  textbook LCS table on 400 random pairs.
- **The trace.** Every round is saved so the path can be walked back from
  the corner. That is O(D squared) memory for D edits, which is why there is a
  cap (`--max-edits`, default 4000): past it, the file is simply replaced
  (all lines deleted, all added), which is still a valid patch and is
  reported as such by `exact=False` in the API.
- **Common ends are free.** The longest shared prefix and suffix are
  removed before searching. Real edits touch a small part of a file, so D is
  tiny in practice.
- **Hunks.** Two changes go in the same hunk when the unchanged gap
  between them is at most twice the context (so their context would
  overlap or touch). Ranges use `diff -u`'s rules: a one-line range writes
  `5` and not `5,1`, and an empty range names the line *before* the gap
  (`-0,0` for an insertion at the very top).
- **Missing final newline.** A last line without `\n` is followed by the
  marker line. A change from `b` to `b\n` shows as a deletion and an
  insertion, because the two lines are not equal.
- **Bytes survive.** Files are decoded as UTF-8 with `surrogateescape`
  and written back the same way, so a stray Latin-1 byte comes out exactly as it
  went in. A NUL in the first 8000 bytes of either file makes it report
  "Binary files ... differ" instead.
- **Not the same text as git.** Where several shortest scripts exist,
  different tie-breaking can choose a different one. Both are valid. The tests
  compare hunk text with `git diff --no-index` only for inputs with one
  shortest script (git also appends the nearest function line after the second
  `@@`, which `diff -u` doesn't, so that is stripped first).
- Not covered: move detection, word diffs, the "patience" and "histogram"
  heuristics for nicer-looking output, `--ignore-space`, and directories.

## Running the tests

```
python -m unittest tests.test_udiff
```

24 tests (about 15 seconds, mostly the ones that start `git`): line
splitting with endings; edit scripts that rebuild `b`, keep every line of
`a` in order and are minimal on 400 random pairs; the paper's ABCABBA example;
the replace-everything fallback; long common stretches; exact hunk text,
range headers, hunk merging at each context size, the end-of-file marker and
CRLF; then the proof that the patches work: 300 random patches (with ragged
ends and context 0, 1, 3 and 8) go through `applypatch.py` forwards and
backwards, 40 more through `git apply`, six hand-picked inputs match
`git diff --no-index`, and the CLI (exit codes, `-U`, non-UTF-8 bytes,
binary files, the cap).
