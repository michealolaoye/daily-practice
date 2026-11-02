# toposort.py

Topological sort that tells you which cycle is the problem. Give it "X
depends on Y" pairs and it returns an order where every item comes after
its dependencies, or the same order grouped into levels that can run in
parallel. Pure stdlib.

## Why

Build steps, migrations, service start-up and package installs all have the
same shape: some things must happen before others. The standard library has
`graphlib`; this is a smaller version of my own that returns levels directly
for the parallel case, keeps a stable order, and has a text format for the
command line. The part I care about most is the error:
"there is a cycle somewhere" is useless in a graph of 200 items, while
`a -> b -> c -> a` tells you what to fix.

## API

```python
from toposort import toposort, levels, CycleError, parse_lines

graph = {"app": ["lib", "db"], "lib": ["core"], "db": ["core"]}

toposort(graph)   # ['core', 'lib', 'db', 'app']
levels(graph)     # [['core'], ['lib', 'db'], ['app']]

try:
    toposort({"a": ["b"], "b": ["c"], "c": ["a"]})
except CycleError as err:
    err.cycle     # ['a', 'b', 'c', 'a']  - each item depends on the next
```

- `toposort(graph)` / `levels(graph)` - `graph` maps an item to an iterable
  of the items it depends on. Items that only appear as dependencies are
  included. Duplicate dependencies are ignored. The input is not modified.
- `CycleError` - a `ValueError` with the cycle in `.cycle`, written
  `[a, b, ..., a]`.
- `parse_lines(lines)` - turns `item: dep dep` text into a graph.

## CLI usage

```
toposort.py [-l] [FILE]     reads 'item: dep dep ...' lines; stdin by default
```

`-l` prints one level per line instead of one item per line.

## Real example

```
$ cat deps.txt
app: lib db
lib: core
db: core
core:
tests: app

$ python scripts/toposort.py deps.txt
core
lib
db
app
tests

$ python scripts/toposort.py -l deps.txt
core
lib db
app
tests

$ printf 'a: b\nb: c\nc: a\nd: a\n' | python scripts/toposort.py
toposort: dependency cycle: a -> b -> c -> a
```

In the input format `#` starts a comment, a line with no `:` or no
dependencies is an item on its own, and the same item on two lines gets both
sets of dependencies.

## Exit codes

- `0` - success.
- `1` - a cycle, a malformed line, or a file that isn't valid UTF-8.
- `2` - usage error or the file could not be opened.

## Design notes

- **The reported cycle is a real one.** When the sort gets stuck, every
  item left over still depends on at least one other left-over item, so
  following any one dependency from any of them must eventually revisit an
  item, and that walk is a cycle. Items that merely lead into the cycle
  (`entry` in `entry -> a -> b -> a`) or hang off it are not in the report.
- **Stable output.** Ties are broken by the order items were first
  mentioned, so the same input always gives the same answer, which matters
  for build logs and diffs.
- **No recursion.** It works level by level (Kahn's algorithm), so a chain
  of 5000 items does not hit Python's recursion limit. A test covers it.
- **Levels are the longest path.** An item goes in the level after its
  deepest dependency, which is the earliest point it could start if each
  level runs in parallel.
- **A self-dependency is a cycle** (`x: x` reports `x -> x`) rather than
  being silently ignored.
- **Nothing is printed on failure.** The CLI builds the whole answer
  first, so a cycle never leaves a half-written order on stdout for a
  following script to act on.

## Running the tests

```
python -m unittest tests/test_toposort.py
```

45 tests: empty and single-item graphs, chains, diamonds, duplicates,
disconnected groups, any iterable of dependencies, non-string items, the
input left unmodified, a 5000-long chain, stable ordering, levels, cycles
(self, two-item, long, only the cycle reported and not what leads into or
out of it, one hidden behind acyclic items), the line parser, and the CLI
including every exit code and invalid UTF-8.
