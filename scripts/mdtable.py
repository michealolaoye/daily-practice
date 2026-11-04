#!/usr/bin/env python3
"""Tidy Markdown tables: pad every cell so the columns line up.

Finds pipe tables in Markdown text (a header row, a delimiter row such as
`| --- | :-: | --: |`, then body rows), rewrites them with aligned columns and
leaves everything else byte for byte as it was. Tables inside fenced code
blocks are not touched. Column widths use display width, so CJK text and
combining accents line up. Pure stdlib."""

import argparse
import os
import re
import sys
import tempfile
import unicodedata

_DELIMITER_CELL = re.compile(r"^:?-+:?$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def display_width(text):
    """Columns `text` occupies in a monospace font: wide (CJK, fullwidth)
    characters count 2, combining marks and zero-width characters 0."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def split_row(line):
    """Split a table row into stripped cell strings. An escaped pipe (`\\|`)
    stays inside its cell; leading and trailing pipes are optional."""
    cells = []
    current = []
    chars = iter(line.strip())
    for ch in chars:
        if ch == "\\":
            nxt = next(chars, "")
            current.append(ch + nxt)
        elif ch == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    stripped = line.strip()
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|") and cells:
        cells = cells[:-1]
    return cells


def parse_alignments(cells):
    """Alignments ('left', 'center', 'right' or None) for a delimiter row, or
    None if `cells` is not a delimiter row."""
    if not cells or not all(_DELIMITER_CELL.match(c) for c in cells):
        return None
    result = []
    for c in cells:
        left, right = c.startswith(":"), c.endswith(":")
        result.append("center" if left and right else "left" if left else "right" if right else None)
    return result


def _pad(text, width, align):
    gap = width - display_width(text)
    if align == "right":
        return " " * gap + text
    if align == "center":
        return " " * (gap // 2) + text + " " * (gap - gap // 2)
    return text + " " * gap


def _delimiter(width, align):
    if align == "center":
        return ":" + "-" * (width - 2) + ":"
    if align == "left":
        return ":" + "-" * (width - 1)
    if align == "right":
        return "-" * (width - 1) + ":"
    return "-" * width


def format_table(header, aligns, rows, indent=""):
    """Render a table as a list of lines. Short rows are padded with empty
    cells. Raises ValueError if a row has more cells than the header."""
    columns = len(header)
    body = []
    for row in rows:
        if len(row) > columns:
            raise ValueError(f"row has {len(row)} cells but the header has {columns}")
        body.append(row + [""] * (columns - len(row)))
    # 3 is the shortest delimiter cell that can show every alignment (":-:").
    widths = [max([3, display_width(header[i])] + [display_width(r[i]) for r in body])
              for i in range(columns)]

    def line(cells):
        return indent + "| " + " | ".join(_pad(c, widths[i], aligns[i]) for i, c in enumerate(cells)) + " |"

    out = [line(header)]
    out.append(indent + "| " + " | ".join(_delimiter(widths[i], aligns[i]) for i in range(columns)) + " |")
    out.extend(line(r) for r in body)
    return out


def _is_row(line):
    return "|" in line and line.strip() != "" and not line.lstrip().startswith(">")


def format_text(text):
    """Return `text` with every Markdown table tidied. Line endings (LF or
    CRLF) and the final newline are preserved. Raises ValueError, naming the
    line, for a table row with more cells than its header."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    fence = None
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            out.append(line)
            i += 1
            continue
        if fence is None and _is_row(line) and i + 1 < len(lines):
            header = split_row(line)
            aligns = parse_alignments(split_row(lines[i + 1])) if _is_row(lines[i + 1]) else None
            # A table needs a delimiter row with as many cells as the header,
            # and an indent of at most three spaces (four is a code block).
            indent = line[: len(line) - len(line.lstrip(" "))]
            if aligns is not None and len(aligns) == len(header) and len(indent) <= 3:
                j = i + 2
                rows = []
                while j < len(lines) and _is_row(lines[j]):
                    cells = split_row(lines[j])
                    if len(cells) > len(header):
                        raise ValueError(f"line {j + 1}: row has {len(cells)} cells but the header has {len(header)}")
                    rows.append(cells)
                    j += 1
                out.extend(format_table(header, aligns, rows, indent))
                i = j
                continue
        out.append(line)
        i += 1
    return eol.join(out)


def _write_atomically(path, text):
    """Replace `path` with `text` so a crash never leaves a half-written file.
    Keeps the original file's permissions."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".mdtable-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="Markdown files (default: stdin, written to stdout)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-i", "--in-place", action="store_true", help="rewrite the files instead of printing")
    mode.add_argument("--check", action="store_true", help="print the files that would change; exit 1 if any")
    args = parser.parse_args(argv)
    if (args.in_place or args.check) and not args.files:
        parser.error("-i and --check need file names")

    status = 0
    changed = []
    for path in args.files or ["-"]:
        try:
            if path == "-":
                text = sys.stdin.buffer.read().decode("utf-8")
            else:
                with open(path, encoding="utf-8", newline="") as handle:
                    text = handle.read()
            result = format_text(text)
            if args.check:
                if result != text:
                    changed.append(path)
            elif args.in_place:
                if result != text:
                    _write_atomically(path, result)
            else:
                sys.stdout.buffer.write(result.encode("utf-8"))
        except OSError as err:
            print(f"mdtable: {err}", file=sys.stderr)
            status = max(status, 2)
        except (ValueError, UnicodeDecodeError) as err:
            print(f"mdtable: {path}: {err}", file=sys.stderr)
            status = max(status, 3)
    for path in changed:
        print(path)
    if status == 0 and changed:
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
