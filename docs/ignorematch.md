# ignorematch.py

Works out which paths a set of `.gitignore` files ignores, without needing
git. It handles the full pattern syntax, nested `.gitignore` files and
`.git/info/exclude`, and can say which line of which file is responsible.
Pure stdlib.

## Why

Plenty of tools need to skip what a project ignores: a backup script, a
packager, a file watcher, a line counter. Shelling out to git doesn't work
outside a repository or in a minimal container, and a quick `fnmatch`
gets the rules wrong. The rules have real corners: anchoring, `**`,
directory-only patterns, and the rule that nothing inside an ignored
directory can be re-included. I wanted those right, and checked against git
itself.

## API

```python
from ignorematch import IgnoreMatcher, Tree

m = IgnoreMatcher()
m.add(open(".gitignore", encoding="utf-8").read().splitlines(), base="", source=".gitignore")
m.is_ignored("build/out.o")            # True
m.why("build/out.o").pattern           # "build/" - the rule responsible

tree = Tree("path/to/project")         # loads .gitignore files as needed
list(tree.walk())                      # every file that is not ignored
tree.why("src/gen/x.py")               # Rule or None; looks at the disk for is_dir
```

- `IgnoreMatcher(ignore_case=False)` with `add(lines, base, source)`. Add
  shallower files before deeper ones, because later rules win.
  - `match(path, is_dir)` gives the last rule matching the path itself (it
    may be a negation).
  - `why(path, is_dir)` gives the rule that makes the path ignored,
    including a parent directory's rule.
  - `is_ignored(path, is_dir)`.
- `Tree(root)` adds the on-disk files: `.git/info/exclude` first, then each
  directory's `.gitignore` when it is reached. It provides `why()` and
  `walk()`.
- `compile_pattern(line)` and `parse(lines)` return `Rule` objects.

## CLI usage

```
ignorematch.py [-C DIR] [-v] [-i] PATH...    print the PATHs that are ignored
ignorematch.py [-C DIR] [-i] --list          print every file that is not ignored

  -C DIR   root of the tree (default: .)
  -v       show source:line:pattern for each ignored path, like git check-ignore -v
  -i       match case-insensitively
```

A PATH with a trailing `/` is treated as a directory; otherwise the disk
decides, and a path that doesn't exist counts as a file.

## Real example

```
$ cat .gitignore
*.log
!keep.log
build/
/TODO
src/**/gen/
$ cat logs/.gitignore
!*.log

$ python scripts/ignorematch.py --list
.gitignore
keep.log
logs/.gitignore
logs/k.txt
logs/z.log
src/TODO
src/main.py

$ python scripts/ignorematch.py -v a.log keep.log build/sub/q TODO src/TODO src/gen/y.py logs/z.log
.gitignore:1:*.log	a.log
.gitignore:3:build/	build/sub/q
.gitignore:4:/TODO	TODO
.gitignore:5:src/**/gen/	src/gen/y.py
```

## Exit codes

- `0` - at least one PATH is ignored (or `--list` succeeded).
- `1` - none of the PATHs are ignored.
- `2` - usage error, a path outside the tree, or an I/O error.

Like `git check-ignore`, so `if ignorematch.py "$f"; then skip; fi` reads
naturally.

## Design notes

- **Last match wins**, across all files. Deeper `.gitignore` files are added
  after shallower ones, so they override them, and `.git/info/exclude` is
  added first, so it has the lowest precedence.
- **Nothing inside an ignored directory comes back.** `why` checks each
  parent directory first. If `build/` is ignored, `!build/keep.txt` has no
  effect, which is what git does. `build/*` plus `!build/keep.txt` does
  work, because the directory itself isn't ignored. `walk` doesn't descend
  into ignored directories, so their `.gitignore` files are never read.
- **Anchoring follows the slash.** A pattern with a `/` at the start or in
  the middle matches relative to its `.gitignore`'s directory. One without
  matches at any depth. A trailing `/` alone doesn't anchor; it means
  "directories only".
- **`**`** as a whole segment means any number of directories: zero or more
  for `**/x` and `a/**/b`, one or more for `a/**`. Elsewhere (`a**b`) it is
  an ordinary `*`.
- **`*` and `?` never match `/`**, and neither does a negated class
  `[!...]`.
- **Escapes are honoured**: `\#`, `\!`, `\*`, `\?`, and `\ ` to keep a
  trailing space, which is otherwise stripped. An unclosed `[` is a literal.
- **Patterns become regular expressions once**, with everything else
  escaped, so `+`, `(` and `.` in file names are plain characters.
- **Case-sensitive by default**, like git with `core.ignorecase=false`. `-i`
  turns it off.
- **Checked against git.** A test builds a real repository and compares the
  answers with `git check-ignore` for 8 pattern sets over 30 paths. It is
  skipped if git isn't installed.

## Running the tests

```
python -m unittest tests/test_ignorematch.py
```

57 tests: parsing (comments, escapes, trailing spaces), wildcards and
classes, anchoring, every form of `**`, precedence and re-inclusion,
nested files, the tree (walk, nested files, ignored directories, `.git`,
`info/exclude`, directories on disk, non-UTF-8 files), the cross-check
against `git check-ignore`, and the CLI including exit codes.
