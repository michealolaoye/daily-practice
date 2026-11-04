# mdtable.py

Tidies Markdown tables so the columns line up. It finds pipe tables in a
Markdown file, rewrites them with padded cells and a matching delimiter row,
and leaves everything else exactly as it was. Pure stdlib.

## Why

Markdown tables are easy to write and miserable to keep tidy: add one longer
cell and every other row in the table is out of line in the source. The
rendered page looks fine, but diffs and code review are harder to read. I
wanted a formatter I can run on a README or a docs folder, and a `--check`
mode to use in a pre-commit hook or CI.

## API

```python
from mdtable import format_text

tidy = format_text(open("README.md", encoding="utf-8", newline="").read())
```

- `format_text(text)` - returns `text` with every table tidied. Line endings
  (LF or CRLF) and the final newline are preserved. Raises `ValueError`,
  naming the line, for a row with more cells than its header.
- Lower level: `split_row`, `parse_alignments`, `format_table`,
  `display_width`.

## CLI usage

```
mdtable.py [-i | --check] [FILE...]

  (no flags)   print the tidied text to stdout (stdin if no FILE)
  -i           rewrite the files in place (only if they would change)
  --check      print the files that would change; exit 1 if any
```

## Real example

```
$ cat notes.md
Name | Qty | Note
:-- | --: | :-:
apples | 3 | ok
日本語 | 12 | a \| b

$ python scripts/mdtable.py notes.md
| Name   | Qty |  Note  |
| :----- | --: | :----: |
| apples |   3 |   ok   |
| 日本語 |  12 | a \| b |

$ python scripts/mdtable.py --check docs/*.md || echo "run mdtable.py -i docs/*.md"
docs/api.md
run mdtable.py -i docs/*.md
```

## Exit codes

- `0` - success (with `--check`: nothing would change).
- `1` - only with `--check`: at least one file would change.
- `2` - usage error or a file could not be read or written.
- `3` - a file could not be formatted: a table row with too many cells, or
  input that is not valid UTF-8. Other files are still processed.

## Design notes

- **A table needs a header, a delimiter row with the same number of cells,
  and at most three spaces of indent.** Anything else is left alone, so prose
  with a stray `|` in it is not touched, and a four-space indent (a code
  block) is not mistaken for a table.
- **Fenced code blocks are skipped**, for both backtick and tilde fences, and
  only the same marker closes one. Quoted (`>`) tables are also left alone.
- **Alignment is kept and used**: `:--` pads on the right, `--:` on the
  left, `:-:` centres (an odd extra space goes on the right). The delimiter
  row is rewritten to the column width, keeping its colons.
- **Widths are display widths.** CJK and fullwidth characters count 2, and
  combining marks and zero-width characters count 0, so `日本語` lines up with
  ASCII in a monospace font.
- **Escaped pipes are content.** `\|` stays inside its cell and counts toward
  its width. Other backslashes are plain text, so a Windows path is fine.
- **Short rows are padded with empty cells**, which is what renderers show
  anyway. A row with *more* cells than the header is an error instead of
  silently dropping data, which is what some renderers do.
- **A row needs a `|`.** A line with no pipe ends the table. Some renderers
  would treat it as a one-cell row; ending the table is the safer reading of
  an unclear case.
- **A minimum column width of 3** so every alignment can be drawn (`:-:`).
- **Line endings are preserved**, and a file with no final newline still has
  none. Files are read and written without newline translation.
- **In-place writes are atomic**: the result goes to a temporary file in the
  same directory and replaces the original, with the original's permissions,
  so an interrupted run never leaves a half-written README. A file that is
  already tidy is not touched, so its modification time is unchanged.
- **It is idempotent**: formatting tidy output changes nothing, which a test
  checks.

## Running the tests

```
python -m unittest tests/test_mdtable.py
```

67 tests (one is skipped on Windows because it checks POSIX permission
bits): display width, row splitting (outer pipes, empty cells, escaped
pipes), alignment parsing, padding and alignment of every kind, wide
characters, short and over-long rows, surrounding text, two tables,
fenced and quoted blocks, indentation, CRLF and final-newline handling,
idempotence, and the CLI (stdin, files, `-i` including atomicity and mtime,
`--check`, every exit code, invalid UTF-8, Unicode round trip).
