#!/usr/bin/env python3
"""Apply a unified diff to a tree of files, all or nothing.

A small stdlib-only `patch`: reads a unified diff (as written by `diff -u` or
`git diff`), checks that every hunk fits, and only then writes anything. Hunks
may land a few lines away from where the diff says; context must match
exactly. Handles new and deleted files, `-R` to undo a patch, `-pN` to strip
path prefixes, and the "\\ No newline at end of file" marker."""

import argparse
import os
import re
import sys
import tempfile

_FILE_HEADER = re.compile(r"^--- (.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NULL = "/dev/null"


class PatchError(Exception):
    """Base class for problems with a patch."""


class MalformedPatch(PatchError):
    """The diff text itself is not a valid unified diff."""


class HunkFailed(PatchError):
    """A hunk does not fit the file it was meant for."""


class Hunk:
    def __init__(self, old_start, new_start, lines, old_noeol, new_noeol):
        self.old_start = old_start
        self.new_start = new_start
        self.lines = lines  # list of (op, text); op is " ", "-" or "+"
        self.old_noeol = old_noeol  # old side ends without a final newline
        self.new_noeol = new_noeol

    def old(self):
        return [t for op, t in self.lines if op != "+"]

    def new(self):
        return [t for op, t in self.lines if op != "-"]

    def reversed(self):
        flipped = [({"+": "-", "-": "+"}.get(op, op), t) for op, t in self.lines]
        return Hunk(self.new_start, self.old_start, flipped, self.new_noeol, self.old_noeol)


class FilePatch:
    def __init__(self, old_path, new_path, hunks):
        self.old_path = old_path
        self.new_path = new_path
        self.hunks = hunks

    def reversed(self):
        return FilePatch(self.new_path, self.old_path, [h.reversed() for h in self.hunks])


def _header_path(raw):
    path = raw.split("\t")[0].strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    return path


def parse_patch(text):
    """Parse unified diff text into a list of FilePatch. Anything before,
    between or after the file sections (`diff --git`, `index`, prose) is
    ignored. Raises MalformedPatch for a broken section."""
    lines = text.replace("\r\n", "\n").split("\n")
    patches = []
    i = 0
    while i < len(lines):
        match = _FILE_HEADER.match(lines[i])
        if not (match and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")):
            i += 1
            continue
        old_path = _header_path(match.group(1))
        new_path = _header_path(lines[i + 1][4:])
        i += 2
        hunks = []
        while i < len(lines):
            header = _HUNK_HEADER.match(lines[i])
            if not header:
                break
            old_start = int(header.group(1))
            old_count = int(header.group(2) if header.group(2) is not None else 1)
            new_start = int(header.group(3))
            new_count = int(header.group(4) if header.group(4) is not None else 1)
            lineno = i + 1
            i += 1
            body = []
            old_seen = new_seen = 0
            old_noeol = new_noeol = False
            while old_seen < old_count or new_seen < new_count:
                if i >= len(lines):
                    raise MalformedPatch(f"line {lineno}: hunk is shorter than its header says")
                line = lines[i]
                op, text_ = (line[:1] or " "), line[1:]  # a bare empty line is an empty context line
                if op == " ":
                    old_seen += 1
                    new_seen += 1
                elif op == "-":
                    old_seen += 1
                elif op == "+":
                    new_seen += 1
                else:
                    raise MalformedPatch(f"line {i + 1}: unexpected {line[:20]!r} inside a hunk")
                body.append((op, text_))
                i += 1
            if old_seen != old_count or new_seen != new_count:
                raise MalformedPatch(f"line {lineno}: hunk has more lines than its header says")
            # The marker follows the last line of whichever side it belongs to.
            while i < len(lines) and lines[i].startswith("\\"):
                if not body:
                    raise MalformedPatch(f"line {i + 1}: stray 'No newline' marker")
                last_op = body[-1][0]
                if last_op in (" ", "-"):
                    old_noeol = True
                if last_op in (" ", "+"):
                    new_noeol = True
                i += 1
            hunks.append(Hunk(old_start, new_start, body, old_noeol, new_noeol))
        if not hunks:
            raise MalformedPatch(f"{new_path}: file section has no hunks")
        patches.append(FilePatch(old_path, new_path, hunks))
    if not patches:
        raise MalformedPatch("no unified diff found")
    return patches


def _split_text(text):
    """(eol, lines, final_newline) for `text`."""
    eol = "\r\n" if "\r\n" in text else "\n"
    parts = text.replace("\r\n", "\n").split("\n")
    if parts[-1] == "":
        return eol, parts[:-1], True
    return eol, parts, False


def _join_text(eol, lines, final_newline):
    if not lines:
        return ""
    return eol.join(lines) + (eol if final_newline else "")


def apply_hunks(text, hunks, name="file"):
    """Return (new_text, notes): `text` with `hunks` applied. Each hunk is
    tried at the line the diff names, then at growing offsets either side;
    context and removed lines must match exactly. Raises HunkFailed."""
    eol, lines, final_newline = _split_text(text)
    notes = []
    delta = 0  # net lines added so far by earlier hunks
    drift = 0  # how far earlier hunks landed from where the diff said
    floor = 0  # earliest index the next hunk may start at
    for number, hunk in enumerate(hunks, 1):
        old, new = hunk.old(), hunk.new()
        # old_start is 1-based, but for a hunk with no old lines it names the
        # line *before* the insertion point.
        expected = (hunk.old_start if not old else hunk.old_start - 1) + delta + drift
        reaches_end = lambda pos: pos + len(old) == len(lines)

        def fits(pos):
            if pos < floor or pos + len(old) > len(lines) or lines[pos:pos + len(old)] != old:
                return False
            if not old:
                return True
            # Only a hunk at the very end may mention a missing final newline.
            return hunk.old_noeol == (reaches_end(pos) and not final_newline)

        found = None
        for distance in range(len(lines) + 1):
            for pos in ((expected,) if distance == 0 else (expected + distance, expected - distance)):
                if fits(pos):
                    found = pos
                    break
            if found is not None:
                break
        if found is None:
            raise HunkFailed(f"{name}: hunk #{number} (at line {hunk.old_start}) does not apply")
        offset = found - expected
        if offset:
            notes.append(f"hunk #{number} applied at line {found + 1} (offset {offset:+d} lines)")
        drift += offset
        if old and reaches_end(found):
            final_newline = not hunk.new_noeol
        elif not old and found == len(lines) and hunk.new_noeol:
            final_newline = False
        lines[found:found + len(old)] = new
        delta += len(new) - len(old)
        floor = found + len(new)
    return _join_text(eol, lines, final_newline), notes


def _target_path(path, strip):
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if len(parts) <= strip:
        raise MalformedPatch(f"cannot strip {strip} leading components from {path!r}")
    parts = parts[strip:]
    if path.startswith(("/", "\\")) and strip == 0 or ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
        raise MalformedPatch(f"refusing path outside the tree: {path!r}")
    return os.path.join(*parts)


def _read(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_atomically(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    mode = os.stat(path).st_mode & 0o7777 if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".applypatch-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply_patch(patch_text, root=".", strip=1, reverse=False, dry_run=False):
    """Apply `patch_text` under `root`. Every file is worked out in memory
    first, so a hunk that fails leaves the tree untouched. Returns the
    progress lines to show. Raises PatchError, or OSError for I/O trouble."""
    patches = parse_patch(patch_text)
    if reverse:
        patches = [p.reversed() for p in patches]
    messages = []
    results = []  # (action, full_path, text)
    seen = set()
    for fp in patches:
        creating = fp.old_path == _NULL
        deleting = fp.new_path == _NULL
        if creating and deleting:
            raise MalformedPatch("a section cannot both create and delete a file")
        if creating:
            rel = _target_path(fp.new_path, strip)
        elif deleting:
            rel = _target_path(fp.old_path, strip)
        else:
            old_rel = _target_path(fp.old_path, strip)
            rel = old_rel if os.path.exists(os.path.join(root, old_rel)) else _target_path(fp.new_path, strip)
        full = os.path.join(root, rel)
        key = os.path.normcase(os.path.abspath(full))
        if key in seen:
            raise MalformedPatch(f"{rel}: patched more than once in one diff")
        seen.add(key)
        if creating:
            if os.path.exists(full):
                raise HunkFailed(f"{rel}: already exists, cannot create it")
            text = ""
        else:
            try:
                text = _read(full)
            except FileNotFoundError:
                raise HunkFailed(f"{rel}: no such file") from None
        new_text, notes = apply_hunks(text, fp.hunks, rel)
        if deleting:
            if new_text != "":
                raise HunkFailed(f"{rel}: not empty after the patch, cannot delete it")
            results.append(("delete", full, None))
            messages.append(f"removing file {rel}")
        else:
            results.append(("write", full, new_text))
            messages.append(f"{'creating' if creating else 'patching'} file {rel}")
        messages.extend("  " + n for n in notes)
    if not dry_run:
        for action, full, new_text in results:
            if action == "delete":
                os.unlink(full)
            else:
                _write_atomically(full, new_text)
    return messages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("patch", nargs="?", help="diff file (default: stdin)")
    parser.add_argument("-p", "--strip", type=int, default=1, metavar="N",
                        help="strip N leading path components (default 1, as for git diff)")
    parser.add_argument("-R", "--reverse", action="store_true", help="undo the patch")
    parser.add_argument("-C", "--directory", default=".", metavar="DIR", help="apply under DIR")
    parser.add_argument("--dry-run", action="store_true", help="check the patch, change nothing")
    args = parser.parse_args(argv)
    if args.strip < 0:
        parser.error("-p must not be negative")
    try:
        if args.patch:
            patch_text = _read(args.patch)
        else:
            patch_text = sys.stdin.buffer.read().decode("utf-8")
        for line in apply_patch(patch_text, args.directory, args.strip, args.reverse, args.dry_run):
            print(line)
    except HunkFailed as err:
        print(f"applypatch: {err}; nothing was changed", file=sys.stderr)
        return 1
    except MalformedPatch as err:
        print(f"applypatch: {err}", file=sys.stderr)
        return 3
    except (OSError, UnicodeDecodeError) as err:
        print(f"applypatch: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
