# applypatch.py

Applies a unified diff to a tree of files, all or nothing. A small `patch`
that checks every hunk fits before it writes anything, and refuses paths that
would land outside the directory. Pure stdlib.

## Why

`patch` and `git apply` are not always there (a minimal container, a Windows
box without Git on the path), and when I do have them I want two things they
don't promise together: nothing changes unless *everything* fits, and a diff
from somewhere else can't write to `../../.ssh`. It was also a good excuse to
get the awkward parts right: hunks that landed a few lines off, files without
a final newline, creating and deleting files, and undoing a patch.

## API

```python
from applypatch import apply_patch, apply_hunks, parse_patch

messages = apply_patch(open("fix.diff", encoding="utf-8").read(), root="src", strip=1)
```

- `apply_patch(patch_text, root=".", strip=1, reverse=False, dry_run=False)` -
  applies every file section under `root`; returns the progress lines.
  Raises `HunkFailed` (a hunk does not fit, a file is missing or already
  exists), `MalformedPatch` (not a valid diff, or a path outside the tree) or
  `OSError`.
- `parse_patch(text)` - list of `FilePatch` objects with their `Hunk`s.
- `apply_hunks(text, hunks)` - `(new_text, notes)` for one file's text.

## CLI usage

```
applypatch.py [-p N] [-R] [-C DIR] [--dry-run] [PATCH]

  PATCH        diff file (stdin if omitted)
  -p N         strip N leading path components (default 1, as for git diff)
  -R           undo the patch
  -C DIR       apply under DIR
  --dry-run    check the patch, change nothing
```

## Real example

```
$ git diff > fix.diff
$ python scripts/applypatch.py -C ../other-checkout fix.diff
patching file src/app.py
  hunk #2 applied at line 118 (offset +6 lines)
creating file tests/test_app.py

$ python scripts/applypatch.py -C ../other-checkout -R fix.diff
patching file src/app.py
removing file tests/test_app.py

$ python scripts/applypatch.py -C ../stale-checkout fix.diff
applypatch: src/app.py: hunk #1 (at line 40) does not apply; nothing was changed
```

## Exit codes

- `0` - applied (or, with `--dry-run`, would apply).
- `1` - a hunk did not fit, a file to patch is missing, or a file to create
  already exists. Nothing was changed.
- `2` - the patch or a target file could not be read or written, or is not
  valid UTF-8; or a usage error.
- `3` - the patch is not a valid unified diff, or names a path outside the
  tree.

## Design notes

- **All or nothing.** Every file is worked out in memory first; only if all
  of them fit are any written. The write phase can still fail on I/O (a full
  disk), which is the one case where some files may already be done, but a
  patch that simply doesn't apply never leaves a half-patched tree.
- **Context must match exactly, position need not.** Each hunk is tried at
  the line the diff names, then one line away either side, then two, and so
  on, and the nearest fit wins. The offset is reported against the diff, so
  "offset +6" means six lines from where the diff said, not from the previous
  hunk. Hunks must stay in order and may not overlap. There is no fuzz: a
  hunk with changed context fails rather than being applied somewhere
  doubtful.
- **Final newlines are tracked.** `\ No newline at end of file` can sit after
  a context line, a removed line (mid-hunk) or an added line, and each case
  is understood. A hunk that mentions a missing final newline can only match
  at the end of a file that really has none, and the other way round.
- **Paths are checked before they are stripped.** With `-p1`,
  `+++ ../../x` would lose its first `..` to the strip and the second would
  go unnoticed if the check ran afterwards. Any `..`, a drive letter, or an
  absolute path with `-p0` is refused (exit 3). Backslashes count as
  separators, so a diff written on Windows is checked the same way.
- **Creating and deleting** follow `/dev/null` in the headers. Creating a
  file that exists is an error, and so is deleting one whose contents the
  patch doesn't account for completely.
- **`-R` swaps the sides** of every hunk and the two paths, so the reverse of
  a creation is a deletion and vice versa.
- **The old name is preferred** when a section has both and the old file
  exists; otherwise the new name is used (a rename that was already done).
- **Line endings stay as they were.** A CRLF file stays CRLF, and the added
  lines take its ending.
- **A file is patched at most once per run**, so two sections for the same
  path are refused instead of depending on their order.
- **Writes are atomic and keep permissions**: a temporary file in the same
  directory, then `os.replace`.
- **Anything outside the file sections is ignored**, so a patch email or a
  `git format-patch` file works as it is.

## Running the tests

```
python -m unittest tests/test_applypatch.py
```

75 tests (one is skipped on Windows because it checks POSIX permission
bits): parsing (headers, timestamps, quoted paths, CRLF, markers, malformed
input), applying (replace, insert, delete, shifted files, drift across
hunks, mismatches, ordering, CRLF, empty files), every final-newline case, a
seeded randomised round trip through `difflib` in both directions, the tree
level (strip levels, create, delete, renames, all-or-nothing, dry run,
reverse, outside paths, duplicates, no leftover temporary files) and the CLI
with every exit code.
