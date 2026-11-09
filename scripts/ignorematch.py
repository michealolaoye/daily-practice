#!/usr/bin/env python3
"""Decide which paths a set of .gitignore files ignores, without git.

Implements the .gitignore pattern rules: `*`, `?`, `[...]`, `**`, `!`
negation, a trailing `/` for directories only, anchoring when the pattern
has a `/`, escapes, nested .gitignore files and `.git/info/exclude`. The
last matching pattern wins, and nothing can be re-included from inside an
ignored directory. Pure stdlib."""

import argparse
import os
import re
import sys


class Rule:
    def __init__(self, pattern, negate, dir_only, regex, source="", lineno=0):
        self.pattern = pattern  # the line as written, without trailing spaces
        self.negate = negate
        self.dir_only = dir_only
        self.regex = regex
        self.source = source
        self.lineno = lineno

    def matches(self, path, is_dir):
        return (is_dir or not self.dir_only) and self.regex.match(path) is not None

    def __repr__(self):
        return f"Rule({self.pattern!r}, {self.source}:{self.lineno})"


def _segment_regex(seg):
    out = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c == "\\" and i + 1 < len(seg):
            out.append(re.escape(seg[i + 1]))
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < len(seg) and seg[j] in "!^":
                j += 1
            if j < len(seg) and seg[j] == "]":
                j += 1  # a "]" first in the class is a literal
            while j < len(seg) and seg[j] != "]":
                j += 1
            if j >= len(seg):
                out.append(re.escape(c))  # no closing bracket: a literal "["
                i += 1
                continue
            body = seg[i + 1:j]
            negate = body[:1] in ("!", "^")
            if negate:
                body = body[1:]
            body = body.replace("\\", "\\\\").replace("[", "\\[")
            out.append("[" + ("^/" if negate else "") + body + "]")
            i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def compile_pattern(line, source="", lineno=0, ignore_case=False):
    """A Rule for one .gitignore line, or None for blank lines and comments."""
    line = line.rstrip("\r\n")
    # Trailing spaces are dropped unless the last one is escaped.
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    if not line or line.startswith("#"):
        return None
    raw = line
    negate = line.startswith("!")
    if negate:
        line = line[1:]
    elif line.startswith(("\\!", "\\#")):
        line = line[1:]
    dir_only = line.endswith("/") and not line.endswith("\\/")
    if dir_only:
        line = line.rstrip("/")
    if not line:
        return None
    anchored = "/" in line
    line = line.lstrip("/")
    segments = line.split("/")
    parts = []
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "**":
            parts.append(".+" if last and i > 0 else ".*" if last else "(?:.*/)?")
        else:
            parts.append(_segment_regex(seg) + ("" if last else "/"))
    prefix = "^" if anchored else "^(?:.*/)?"
    regex = re.compile(prefix + "".join(parts) + "$", re.IGNORECASE if ignore_case else 0)
    return Rule(raw, negate, dir_only, regex, source, lineno)


def parse(lines, source="", ignore_case=False):
    """Rules for the lines of one .gitignore file."""
    rules = []
    for n, line in enumerate(lines, 1):
        rule = compile_pattern(line, source, n, ignore_case)
        if rule is not None:
            rules.append(rule)
    return rules


class IgnoreMatcher:
    """Rules from any number of .gitignore files, each tied to the directory
    it lives in. Add shallower files before deeper ones: later rules win."""

    def __init__(self, ignore_case=False):
        self.ignore_case = ignore_case
        self._rules = []  # (base, Rule)

    def add(self, lines, base="", source=""):
        base = base.strip("/")
        for rule in parse(lines, source, self.ignore_case):
            self._rules.append((base, rule))

    def match(self, path, is_dir=False):
        """The last rule matching `path` itself (which may be a negation), or
        None. Parent directories are not considered; see `why`."""
        path = path.strip("/")
        found = None
        for base, rule in self._rules:
            if base:
                if not path.startswith(base + "/"):
                    continue
                rel = path[len(base) + 1:]
            else:
                rel = path
            if rule.matches(rel, is_dir):
                found = rule
        return found

    def why(self, path, is_dir=False):
        """The rule that makes `path` ignored, or None if it is not. A path
        inside an ignored directory is ignored by that directory's rule,
        whatever later patterns say."""
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            rule = self.match("/".join(parts[:i]), True)
            if rule is not None and not rule.negate:
                return rule
        rule = self.match("/".join(parts), is_dir)
        return rule if rule is not None and not rule.negate else None

    def is_ignored(self, path, is_dir=False):
        return self.why(path, is_dir) is not None


def _read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            return handle.read().splitlines()
    except FileNotFoundError:
        return None


class Tree:
    """An IgnoreMatcher that loads .gitignore files from a directory tree on
    demand, plus `.git/info/exclude` (lowest precedence)."""

    def __init__(self, root, ignore_case=False):
        self.root = root
        self.matcher = IgnoreMatcher(ignore_case)
        self._loaded = set()
        exclude = os.path.join(root, ".git", "info", "exclude")
        lines = _read_lines(exclude)
        if lines is not None:
            self.matcher.add(lines, "", ".git/info/exclude")
        self._load("")

    def _load(self, rel_dir):
        if rel_dir in self._loaded:
            return
        self._loaded.add(rel_dir)
        source = (rel_dir + "/" if rel_dir else "") + ".gitignore"
        lines = _read_lines(os.path.join(self.root, *source.split("/")))
        if lines is not None:
            self.matcher.add(lines, rel_dir, source)

    def why(self, rel_path, is_dir=None):
        parts = rel_path.strip("/").split("/")
        for i in range(1, len(parts)):
            self._load("/".join(parts[:i]))
        if is_dir is None:
            is_dir = os.path.isdir(os.path.join(self.root, *parts))
        return self.matcher.why(rel_path, is_dir)

    def walk(self):
        """Yield every file that is not ignored, as a relative path with `/`
        separators, sorted. Ignored directories are not descended into, and
        `.git` is always skipped."""
        def visit(rel_dir):
            self._load(rel_dir)
            full = os.path.join(self.root, *rel_dir.split("/")) if rel_dir else self.root
            with os.scandir(full) as entries:
                items = sorted(entries, key=lambda e: e.name)
            for entry in items:
                rel = (rel_dir + "/" if rel_dir else "") + entry.name
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir and entry.name == ".git":
                    continue
                rule = self.matcher.match(rel, is_dir)
                if rule is not None and not rule.negate:
                    continue  # parents were checked on the way down
                if is_dir:
                    yield from visit(rel)
                else:
                    yield rel
        yield from visit("")


def _relative(root, path):
    """`path` (relative to the current directory or absolute) as a path
    relative to `root`, with `/` separators. Raises ValueError if outside."""
    keep_dir = path.endswith(("/", "\\"))
    full = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
    rel = os.path.relpath(full, os.path.abspath(root))
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise ValueError(f"{path}: outside {root}")
    return rel.replace(os.sep, "/") + ("/" if keep_dir else "")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", help="paths to check, relative to DIR (a trailing / means a directory)")
    parser.add_argument("-C", "--directory", default=".", metavar="DIR", help="root of the tree (default: .)")
    parser.add_argument("-v", "--why", action="store_true", help="show the source, line and pattern responsible")
    parser.add_argument("--list", action="store_true", help="list every file that is not ignored")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="match case-insensitively")
    args = parser.parse_args(argv)
    if args.list == bool(args.paths):
        parser.error("give either PATHs to check or --list")
    if not os.path.isdir(args.directory):
        print(f"ignorematch: {args.directory}: not a directory", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    try:
        tree = Tree(args.directory, args.ignore_case)
        if args.list:
            for rel in tree.walk():
                print(rel)
            return 0
        any_ignored = False
        for path in args.paths:
            rel = _relative(args.directory, path)
            rule = tree.why(rel.rstrip("/"), True if rel.endswith("/") else None)
            if rule is None:
                continue
            any_ignored = True
            print(f"{rule.source}:{rule.lineno}:{rule.pattern}\t{path}" if args.why else path)
        return 0 if any_ignored else 1
    except ValueError as err:
        print(f"ignorematch: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"ignorematch: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
