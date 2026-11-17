#!/usr/bin/env python3
"""Release notes and the next version number from Conventional Commits.

Reads `git log` since the last tag, groups `feat:`, `fix:` and `perf:`
commits (and anything marked breaking) into a Markdown section, and works
out the semver bump: breaking -> major, feat -> minor, fix/perf -> patch.
Pure stdlib; git is run with an argument list, never through a shell."""

import argparse
import datetime
import re
import subprocess
import sys

HEADER = re.compile(r"(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]+)\))?(?P<bang>!)?: (?P<desc>\S.*)\Z")
FOOTER = re.compile(r"(?P<token>BREAKING CHANGE|BREAKING-CHANGE|[A-Za-z][A-Za-z-]*)(?:: | #)")
VERSION = re.compile(r"(?P<prefix>v?)(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)\Z")

SECTIONS = [("feat", "Features"), ("fix", "Bug fixes"), ("perf", "Performance")]
RANK = {None: 0, "patch": 1, "minor": 2, "major": 3}


class GitError(RuntimeError):
    pass


class Commit:
    def __init__(self, sha, type_, scope, description, breaking, notes):
        self.sha = sha
        self.type = type_          # lower-cased
        self.scope = scope
        self.description = description
        self.breaking = breaking
        self.notes = notes         # text of BREAKING CHANGE footers

    def __repr__(self):
        return f"Commit({self.sha[:7]} {self.type}{'!' if self.breaking else ''}: {self.description!r})"


def _breaking_notes(lines):
    """Text of every BREAKING CHANGE footer. A footer runs until the next
    footer token or the end of the message, so it can span lines."""
    notes = []
    current = None
    for line in lines:
        m = FOOTER.match(line)
        if m:
            if current is not None:
                notes.append("\n".join(current).strip())
            token = m.group("token")
            current = [line[m.end():]] if token in ("BREAKING CHANGE", "BREAKING-CHANGE") else None
        elif current is not None:
            current.append(line)
    if current is not None:
        notes.append("\n".join(current).strip())
    return [n for n in notes if n]


def parse_commit(message, sha=""):
    """A Commit, or None if the header isn't a Conventional Commit."""
    lines = message.strip("\n").splitlines()
    if not lines:
        return None
    m = HEADER.match(lines[0].rstrip())
    if not m:
        return None
    # footers live in the last paragraph, after a blank line
    body = lines[1:]
    last_blank = max((i for i, line in enumerate(body) if not line.strip()), default=None)
    footer_lines = body[last_blank + 1:] if last_blank is not None else []
    notes = _breaking_notes(footer_lines)
    scope = m.group("scope").strip() if m.group("scope") else None
    return Commit(sha, m.group("type").lower(), scope, m.group("desc").strip(),
                  bool(m.group("bang")) or bool(notes), notes)


def bump_for(commits, current=None):
    """'major', 'minor', 'patch' or None. Before 1.0.0 a breaking change only
    bumps the minor version, as semver allows for initial development."""
    level = None
    for c in commits:
        if c.breaking:
            this = "major"
        elif c.type == "feat":
            this = "minor"
        elif c.type in ("fix", "perf"):
            this = "patch"
        else:
            continue
        if RANK[this] > RANK[level]:
            level = this
    if level == "major" and current is not None and parse_version(current)[1] == 0:
        level = "minor"
    return level


def parse_version(text):
    """(prefix, major, minor, patch) from 'v1.2.3' or '1.2.3'."""
    m = VERSION.match(text.strip())
    if not m:
        raise ValueError(f"not a version like 1.2.3 or v1.2.3: {text!r}")
    return m.group("prefix"), int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def next_version(current, bump):
    prefix, major, minor, patch = parse_version(current)
    if bump == "major":
        return f"{prefix}{major + 1}.0.0"
    if bump == "minor":
        return f"{prefix}{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{prefix}{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump {bump!r}")


def _line(c, short=7):
    scope = f"**{c.scope}:** " if c.scope else ""
    sha = f" ({c.sha[:short]})" if c.sha else ""
    return f"- {scope}{c.description}{sha}"


def render(version, date, commits, include_other=False):
    """The Markdown section for one release."""
    out = [f"## {version} ({date})", ""]
    breaking = [c for c in commits if c.breaking]
    if breaking:
        out += ["### Breaking changes", ""]
        for c in breaking:
            out.append(_line(c))
            for note in c.notes:
                out += ["  " + line.strip() if line.strip() else "" for line in note.splitlines()]
        out.append("")
    for type_, title in SECTIONS:
        group = [c for c in commits if c.type == type_ and not c.breaking]
        if group:
            out += [f"### {title}", ""] + [_line(c) for c in group] + [""]
    if include_other:
        other = [c for c in commits if not c.breaking and c.type not in dict(SECTIONS)]
        if other:
            out += ["### Other changes", ""] + [_line(c).replace("- ", f"- {c.type}: ", 1) for c in other] + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def _git(repo, *args):
    try:
        proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout


def last_tag(repo):
    """The most recent tag reachable from HEAD, or None if there are none."""
    try:
        return _git(repo, "describe", "--tags", "--abbrev=0").strip() or None
    except GitError as err:
        if "No names found" in str(err) or "No tags can describe" in str(err):
            return None
        raise


def read_commits(repo, since=None):
    """(conventional commits, non-conventional subjects) after `since`,
    oldest first. Merge commits are skipped."""
    if since and since.startswith("-"):
        raise ValueError(f"not a revision: {since!r}")  # would be read as a git option
    rev = f"{since}..HEAD" if since else "HEAD"
    raw = _git(repo, "log", "--no-merges", "--reverse", "--format=%H%x00%B%x1e", rev, "--")
    commits, others = [], []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, message = record.partition("\x00")
        c = parse_commit(message, sha)
        if c:
            commits.append(c)
        else:
            others.append((sha, message.strip().splitlines()[0] if message.strip() else ""))
    return commits, others


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-C", dest="repo", default=".", metavar="DIR", help="the git repository (default: .)")
    parser.add_argument("--since", metavar="REF", help="start after REF instead of the last tag")
    parser.add_argument("--current", metavar="VERSION",
                        help="current version, if the last tag isn't one (default: the tag, or 0.0.0)")
    parser.add_argument("--date", help="release date (default: today)")
    parser.add_argument("--all", action="store_true", help="also list docs:, chore:, refactor: and other types")
    parser.add_argument("--next", action="store_true", help="print only the next version")
    parser.add_argument("--strict", action="store_true", help="fail if any commit isn't a Conventional Commit")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    date = args.date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, not {date!r}")
    try:
        tag = last_tag(args.repo) if args.since is None else None
        since = args.since or tag
        current = args.current or tag or "0.0.0"
        parse_version(current)
        commits, others = read_commits(args.repo, since)
    except GitError as err:
        print(f"changelog: {err}", file=sys.stderr)
        return 2
    except ValueError as err:
        print(f"changelog: {err} (use --current)", file=sys.stderr)
        return 2
    for sha, subject in others:
        print(f"changelog: not a Conventional Commit: {sha[:7]} {subject}", file=sys.stderr)
    if args.strict and others:
        return 1
    bump = bump_for(commits, current)
    if bump is None:
        print(f"changelog: nothing to release since {since or 'the first commit'}", file=sys.stderr)
        return 1
    version = next_version(current, bump)
    if args.next:
        print(version)
    else:
        print(render(version, date, commits, args.all), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
