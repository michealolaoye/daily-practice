#!/usr/bin/env python3
"""Topological sort that tells you which cycle is the problem.

Given "X depends on Y" pairs it returns an order where every item comes after
the things it depends on, or the same order grouped into levels that could
run in parallel. When there is a cycle it raises `CycleError` carrying one
actual cycle (a -> b -> a), not just "a cycle exists". Ties keep the order
items were first mentioned, so the output is stable. Pure stdlib."""

import sys


class CycleError(ValueError):
    """The dependencies contain a cycle. `cycle` lists it as
    [a, b, ..., a], where each item depends on the next one."""

    def __init__(self, cycle):
        self.cycle = cycle
        super().__init__("dependency cycle: " + " -> ".join(str(n) for n in cycle))


def levels(graph):
    """Group items into levels: every item in a level depends only on items
    in earlier levels, so a level can be run in parallel.

    `graph` maps an item to an iterable of the items it depends on. Items
    that only appear as dependencies are included. Duplicate dependencies are
    ignored. Raises CycleError if there is a cycle."""
    deps = {}
    for node, wanted in graph.items():
        deps.setdefault(node, set())
        for dep in wanted:
            deps[node].add(dep)
            deps.setdefault(dep, set())

    # `order` is first-mention order, used to break ties deterministically.
    order = {}
    for node, wanted in graph.items():
        order.setdefault(node, len(order))
        for dep in wanted:
            order.setdefault(dep, len(order))

    dependents = {node: [] for node in deps}
    waiting = {}
    for node, wanted in deps.items():
        waiting[node] = len(wanted)
        for dep in wanted:
            dependents[dep].append(node)

    result = []
    ready = sorted((n for n in deps if waiting[n] == 0), key=order.__getitem__)
    placed = 0
    while ready:
        result.append(ready)
        placed += len(ready)
        following = []
        for node in ready:
            for user in dependents[node]:
                waiting[user] -= 1
                if waiting[user] == 0:
                    following.append(user)
        ready = sorted(following, key=order.__getitem__)

    if placed != len(deps):
        stuck = {n for n in deps if waiting[n] > 0}
        raise CycleError(_find_cycle(deps, stuck, order))
    return result


def toposort(graph):
    """Return a flat list in which every item comes after its dependencies.
    Same input and errors as `levels`."""
    return [node for level in levels(graph) for node in level]


def _find_cycle(deps, stuck, order):
    """Every item in `stuck` still depends on at least one other stuck item
    (otherwise it would have been placed), so following any one dependency
    from any stuck item must eventually revisit an item. That walk is a
    cycle."""
    node = min(stuck, key=order.__getitem__)
    seen = {}
    path = []
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = min((d for d in deps[node] if d in stuck), key=order.__getitem__)
    return path[seen[node]:] + [node]


def parse_lines(lines):
    """Parse text of the form `item: dep dep ...` (one item per line, `#`
    starts a comment, an item with no `:` or no dependencies stands alone)
    into a graph. The same item on two lines accumulates its dependencies.
    Raises ValueError, naming the line, for a line with an empty item."""
    graph = {}
    for number, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or len(name.split()) != 1:
            raise ValueError(f"line {number}: expected 'item: dep dep ...', got {raw.strip()!r}")
        graph.setdefault(name, []).extend(rest.split())
    return graph


USAGE = "usage: toposort.py [-l] [FILE]   (reads 'item: dep dep ...' lines; stdin by default)"


def main(argv):
    show_levels = False
    path = None
    for arg in argv:
        if arg == "-l":
            show_levels = True
        elif arg.startswith("-") and arg != "-":
            print(USAGE, file=sys.stderr)
            return 2
        elif path is None:
            path = arg
        else:
            print(USAGE, file=sys.stderr)
            return 2

    try:
        if path is None or path == "-":
            graph = parse_lines(sys.stdin)
        else:
            with open(path, encoding="utf-8") as handle:
                graph = parse_lines(handle)
    except OSError as err:
        print(f"toposort: {err}", file=sys.stderr)
        return 2
    except (ValueError, UnicodeDecodeError) as err:
        print(f"toposort: {err}", file=sys.stderr)
        return 1

    try:
        grouped = levels(graph)
    except CycleError as err:
        print(f"toposort: {err}", file=sys.stderr)
        return 1

    for level in grouped:
        print(" ".join(level) if show_levels else "\n".join(level))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
