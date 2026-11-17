# changelog.py

Turns `git log` since the last tag into release notes and works out the next
version number from [Conventional Commits](https://www.conventionalcommits.org/):
breaking changes bump the major version, `feat:` the minor, and `fix:` or
`perf:` the patch. Pure stdlib, plus git.

## Why

A deploy pipeline that tags releases has to answer two questions every
time: what's the next version, and what changed? Answered by hand, the
version is a guess and the notes get skipped. If the commits follow
Conventional Commits, both answers are already in the history. Commits that
don't follow it are named on stderr rather than quietly lost, and
`--strict` turns them into a failure.

## API

```python
from changelog import parse_commit, bump_for, next_version, render, read_commits, last_tag

c = parse_commit("feat(api)!: drop v1\n\nBREAKING CHANGE: use /v2", sha="abc1234...")
c.type, c.scope, c.description, c.breaking, c.notes
# ("feat", "api", "drop v1", True, ["use /v2"])

bump_for([c], current="v1.4.0")          # "major" ("minor" if current is 0.x)
next_version("v1.4.0", "major")          # "v2.0.0"
print(render("v2.0.0", "2026-11-12", [c]))

tag = last_tag(".")                       # "v1.4.0", or None with no tags
commits, others = read_commits(".", tag)  # oldest first; others = (sha, subject) that didn't parse
```

## CLI usage

```
changelog.py [-C DIR] [--since REF] [--current VERSION] [--date YYYY-MM-DD]
             [--all] [--next] [--strict]

  -C DIR        repository (default: .)
  --since REF   start after REF instead of the last tag
  --current V   current version if the last tag isn't one (default: the tag, or 0.0.0)
  --date D      date in the heading (default: today)
  --all         also list docs:, chore:, refactor: and other types
  --next        print only the next version
  --strict      exit 1 if any commit isn't a Conventional Commit
```

## Real example

A demo repository built for this (in a scratch directory):

```
$ git log --oneline v1.2.0..HEAD
9a72ae5 perf: cache the release manifest
54171e2 feat(config)!: read settings from TOML
baae01d Update stuff
c189398 docs: deployment notes
ddca510 feat(cli): add --dry-run
d791145 fix(deploy): wait for migrations before switching traffic

$ python changelog.py --date 2026-11-12
changelog: not a Conventional Commit: baae01d Update stuff
## v2.0.0 (2026-11-12)

### Breaking changes

- **config:** read settings from TOML (54171e2)
  settings.ini is no longer read.
  Convert it with scripts/ini2toml.py first.

### Features

- **cli:** add --dry-run (ddca510)

### Bug fixes

- **deploy:** wait for migrations before switching traffic (d791145)

### Performance

- cache the release manifest (9a72ae5)

$ python changelog.py --next 2>/dev/null
v2.0.0
$ python changelog.py --strict >/dev/null; echo $?
changelog: not a Conventional Commit: baae01d Update stuff
1
$ git tag v2.0.0 && python changelog.py; echo $?
changelog: nothing to release since v2.0.0
1
```

In a pipeline: `if v=$(python changelog.py --next); then git tag "$v"; fi`.

## Exit codes

- `0` - notes (or the version) printed.
- `1` - nothing releasable since the last tag (only `docs:`, `chore:` and
  similar, or no commits at all), or `--strict` found commits that aren't
  Conventional Commits.
- `2` - usage error, not a git repository, unknown revision, or a version
  that can't be parsed (`--current` fixes the last case).

## Design notes

- **Bump rules.** A breaking change means major, `feat` minor, `fix` and
  `perf` patch, and the highest one wins. Other types don't trigger a
  release on their own. Before 1.0.0 a breaking change only bumps the
  minor version (`v0.3.1` becomes `v0.4.0`), which semver allows during
  initial development.
- **What counts as breaking.** A `!` before the colon, or a
  `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer. Footers are only looked
  for in the last paragraph, which is where the spec puts them, so a body
  that merely mentions "BREAKING CHANGE:" in passing doesn't trigger a
  major release. The token must be upper case. A footer's text runs until
  the next footer token (`Refs #12`, `Reviewed-by: ...`), so multi-line
  notes are kept.
- **Strict header.** `type(scope)!: description` with exactly one space
  after the colon. `feat:x`, `feat ():` and `feat(): x` aren't Conventional
  Commits. The type is case-insensitive.
- **Merges are skipped** (`--no-merges`). The commits they bring in are
  listed instead, and "Merge branch ..." isn't reported as
  non-conventional.
- **Oldest first**, so the notes read in the order the work happened.
- **The tag's prefix is kept**: `v1.4.0` gives `v1.4.1` and `1.4.0` gives
  `1.4.1`. With no tags, all history counts and the current version is
  `0.0.0`. A tag that isn't a plain `X.Y.Z` stops the run instead of
  guessing; `--current` says what it should have been.
- **git is run with an argument list**, never a shell, and a `--since`
  value starting with `-` is refused, since git would read it as an option
  (`--since=--output=x` could otherwise write a file).

## Running the tests

```
python -m unittest tests/test_changelog.py
```

34 tests: header parsing (scopes, case, everything that isn't
conventional), `!` and both footer spellings, footers spanning lines,
footers only in the last paragraph, CRLF, bump rules including pre-1.0,
versions, and rendering. Against real temporary git repositories: since
the last tag, `--next`, no tags, order, nothing to release, `--strict`,
merge commits, `--since`, non-version tags, Unicode messages, option-like
revisions, and the error exits. The repository tests are skipped if git
isn't installed.
