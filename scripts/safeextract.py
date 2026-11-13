#!/usr/bin/env python3
"""Extract a tar or zip archive without letting it write outside the target.

Every member is checked before anything is written: no absolute paths or
`..`, no names Windows can't hold, no device files, no duplicates, nothing
inside a symlink, and links (only with --links) must stay inside. Then the
archive is extracted into a temporary directory next to DEST, with the
size and file-count limits enforced on the bytes actually written, and
renamed into place only if every member made it. Pure stdlib."""

import argparse
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import zipfile

DEFAULT_MAX_SIZE = 1 << 30
DEFAULT_MAX_FILES = 10_000
MAX_LINK_TARGET = 4096
CHUNK = 1 << 16
RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
SIZE = re.compile(r"(\d+)([KMG]?)", re.I)


class UnsafeArchive(Exception):
    """The archive was refused; `problems` lists (member name, reason)."""

    def __init__(self, problems):
        super().__init__(f"{len(problems)} unsafe member(s)")
        self.problems = problems


class Member:
    def __init__(self, name, kind, size=0, mode=0o644, link=None, mtime=None, source=None):
        self.name = name        # as stored in the archive
        self.kind = kind        # "file", "dir", "symlink", "hardlink" or "special"
        self.size = size        # declared size; zip headers can lie, so writes are counted too
        self.mode = mode
        self.link = link        # link target as stored
        self.mtime = mtime
        self.source = source    # TarInfo or ZipInfo
        self.parts = None       # normalised path components, filled in by check()
        self.target = None      # for hard links, the parts of the file they name

    def __repr__(self):
        return f"Member({self.name!r}, {self.kind})"


def split_name(name):
    """The path components of a member name, or raise ValueError saying why
    it can't be extracted anywhere safely. `./a//b/` is ("a", "b")."""
    if name == "" or "\x00" in name:
        raise ValueError("empty name or NUL byte")
    if any(ord(ch) < 0x20 for ch in name):
        raise ValueError("control character in name")
    if "\\" in name:
        raise ValueError("backslash in name")
    if name.startswith("/") or re.match(r"[A-Za-z]:", name):
        raise ValueError("absolute path")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    for part in parts:
        if part == "..":
            raise ValueError("'..' in path")
        if ":" in part:
            raise ValueError("':' in name")
        if part.endswith((".", " ")):
            raise ValueError("name ends in a dot or space")
        if part.split(".")[0].lower() in RESERVED:
            raise ValueError(f"reserved name on Windows: {part}")
    return tuple(parts)


def _tar_members(tf):
    out = []
    for info in tf.getmembers():
        if info.isreg():
            kind = "file"
        elif info.isdir():
            kind = "dir"
        elif info.issym():
            kind = "symlink"
        elif info.islnk():
            kind = "hardlink"
        else:
            kind = "special"
        out.append(Member(info.name, kind, info.size if kind == "file" else 0, info.mode,
                          info.linkname or None, info.mtime, info))
    return out


def _zip_members(zf):
    out = []
    for info in zf.infolist():
        mode = info.external_attr >> 16
        mtime = time.mktime(info.date_time + (0, 0, -1))
        if info.flag_bits & 0x1:
            out.append(Member(info.filename, "encrypted", source=info))
        elif info.is_dir():
            out.append(Member(info.filename, "dir", mode=mode or 0o755, mtime=mtime, source=info))
        elif stat.S_ISLNK(mode):
            link = None
            if info.file_size <= MAX_LINK_TARGET:
                with zf.open(info) as fh:
                    link = fh.read(MAX_LINK_TARGET + 1).decode("utf-8", "replace")
            out.append(Member(info.filename, "symlink", link=link, mtime=mtime, source=info))
        elif stat.S_IFMT(mode) and not stat.S_ISREG(mode):  # many zips store permissions with no type
            out.append(Member(info.filename, "special", source=info))
        else:
            out.append(Member(info.filename, "file", info.file_size, mode or 0o644, mtime=mtime, source=info))
    return out


def open_archive(path):
    """("zip", ZipFile) or ("tar", TarFile) for a path; ValueError if it's neither."""
    if zipfile.is_zipfile(path):
        return "zip", zipfile.ZipFile(path)
    try:
        return "tar", tarfile.open(path, "r:*")
    except tarfile.TarError as err:
        raise ValueError(f"not a tar or zip archive ({err})") from None


def read_members(kind, archive):
    return _zip_members(archive) if kind == "zip" else _tar_members(archive)


def _fold(parts):
    return tuple(p.casefold() for p in parts)


def _symlink_target(member, links):
    """The components a symlink points at, walked one step at a time from
    its own directory, or raise ValueError. `links` holds the folded paths of
    every symlink in the archive: stepping *through* one is refused, because
    `s/../x` with `s -> .` looks like `x` but really lands outside."""
    target = member.link
    if not target:
        raise ValueError("link with no target")
    if "\\" in target or target.startswith("/") or re.match(r"[A-Za-z]:", target):
        raise ValueError(f"link to an absolute path: {target}")
    where = list(member.parts[:-1])
    steps = [p for p in target.split("/") if p not in ("", ".")]
    for i, step in enumerate(steps):
        if step == "..":
            if not where:
                raise ValueError(f"link points outside: {target}")
            where.pop()
            continue
        where.append(step)
        if i < len(steps) - 1 and _fold(where) in links:
            raise ValueError(f"link goes through another link: {target}")
    return tuple(where)


def check(members, *, allow_links=False, max_files=DEFAULT_MAX_FILES, max_size=DEFAULT_MAX_SIZE):
    """Every problem with the archive as (member name, reason); empty if it's
    safe to extract. Fills in `parts` on the members it accepts."""
    problems = []
    seen = {}
    for m in members:
        try:
            m.parts = split_name(m.name)
        except ValueError as err:
            problems.append((m.name, str(err)))
            continue
        if not m.parts:
            if m.kind != "dir":
                problems.append((m.name, "names the destination itself"))
            continue
        if m.kind == "special":
            problems.append((m.name, "device, FIFO or other special file"))
        elif m.kind == "encrypted":
            problems.append((m.name, "encrypted"))
        elif m.kind in ("symlink", "hardlink") and not allow_links:
            problems.append((m.name, f"{m.kind} (allow with --links)"))
        key = _fold(m.parts)
        earlier = seen.get(key)
        if earlier is not None and not (earlier.kind == m.kind == "dir"):
            same = "appears twice" if earlier.parts == m.parts else f"differs only in case from {earlier.name}"
            problems.append((m.name, same))
        seen.setdefault(key, m)

    # Nothing may sit underneath something that isn't a directory, whatever
    # order the members come in; with symlinks that would write through them.
    for key, m in seen.items():
        for i in range(1, len(key)):
            parent = seen.get(key[:i])
            if parent is not None and parent.kind != "dir":
                problems.append((m.name, f"inside {parent.kind} {parent.name}"))
                break

    if allow_links:
        links = {key for key, m in seen.items() if m.kind == "symlink"}
        for m in seen.values():
            try:
                if m.kind == "symlink":
                    _symlink_target(m, links)
                elif m.kind == "hardlink":
                    # A hard link names another member, relative to the root.
                    linked = seen.get(_fold(split_name(m.link or "")))
                    if linked is None or linked.kind != "file":
                        raise ValueError(f"hard link to something that isn't a file in the archive: {m.link}")
                    m.target = linked.parts
            except ValueError as err:
                problems.append((m.name, str(err)))

    files = [m for m in members if m.kind != "dir"]
    if len(members) > max_files:
        problems.append(("", f"{len(members)} members, more than the limit of {max_files}"))
    total = sum(m.size for m in files)
    if total > max_size:
        problems.append(("", f"{total} bytes uncompressed, more than the limit of {max_size}"))
    return problems


def _open_member(kind, archive, m):
    if kind == "zip":
        return archive.open(m.source)
    fh = archive.extractfile(m.source)
    if fh is None:
        raise ValueError(f"can't read {m.name}")
    return fh


def _write_file(src, path, budget):
    """Copy src to a new file at path; return bytes written. Raises
    UnsafeArchive once more than `budget` bytes have been written."""
    written = 0
    with open(path, "xb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                return written
            written += len(chunk)
            if written > budget:
                raise UnsafeArchive([("", "uncompressed data is larger than its headers say and over the size limit")])
            out.write(chunk)


def extract(archive_path, dest, *, allow_links=False, max_files=DEFAULT_MAX_FILES, max_size=DEFAULT_MAX_SIZE):
    """Extract into `dest`, which must not exist yet. All or nothing: on any
    error the partial tree is removed and `dest` is never created. Returns
    the number of members and bytes written."""
    dest = os.path.abspath(dest)
    if os.path.lexists(dest):
        raise FileExistsError(f"{dest} already exists")
    kind, archive = open_archive(archive_path)
    with archive:
        members = read_members(kind, archive)
        problems = check(members, allow_links=allow_links, max_files=max_files, max_size=max_size)
        if problems:
            raise UnsafeArchive(problems)
        tmp = tempfile.mkdtemp(prefix=".safeextract-", dir=os.path.dirname(dest))
        try:
            total = 0
            # Links last, so no regular member is ever written after a link exists.
            ordered = [m for m in members if m.parts and m.kind in ("dir", "file")]
            ordered += [m for m in members if m.parts and m.kind in ("symlink", "hardlink")]
            for m in ordered:
                path = os.path.join(tmp, *m.parts)
                if m.kind == "dir":
                    os.makedirs(path, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if m.kind == "file":
                    with _open_member(kind, archive, m) as src:
                        total += _write_file(src, path, max_size - total)
                    os.chmod(path, 0o755 if m.mode & 0o111 else 0o644)
                    if m.mtime is not None:
                        os.utime(path, (m.mtime, m.mtime))
                elif m.kind == "symlink":
                    os.symlink(m.link, path)
                else:  # a hard link becomes a copy, so nothing outside can be linked to
                    shutil.copy2(os.path.join(tmp, *m.target), path)
            os.rename(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return len(members), total


def parse_size(text):
    m = SIZE.fullmatch(text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"not a size: {text!r} (use e.g. 500M)")
    return int(m.group(1)) << {"": 0, "K": 10, "M": 20, "G": 30}[m.group(2).upper()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("archive", help="a tar (optionally gz, bz2 or xz) or zip file")
    parser.add_argument("dest", nargs="?", help="directory to create; must not exist")
    parser.add_argument("--check", action="store_true", help="only report problems, extract nothing")
    parser.add_argument("--links", action="store_true", help="allow symlinks and hard links that stay inside")
    parser.add_argument("--max-size", type=parse_size, default=DEFAULT_MAX_SIZE, metavar="SIZE",
                        help="limit on uncompressed bytes, e.g. 200M (default: 1G)")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, metavar="N",
                        help=f"limit on members (default: {DEFAULT_MAX_FILES})")
    args = parser.parse_args(argv)
    if not args.check and args.dest is None:
        parser.error("DEST is required unless --check is given")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    limits = dict(allow_links=args.links, max_files=args.max_files, max_size=args.max_size)
    try:
        if args.check:
            kind, archive = open_archive(args.archive)
            with archive:
                members = read_members(kind, archive)
            problems = check(members, **limits)
            if problems:
                raise UnsafeArchive(problems)
            print(f"{args.archive}: {len(members)} members, nothing unsafe")
            return 0
        count, total = extract(args.archive, args.dest, **limits)
    except UnsafeArchive as err:
        for name, reason in err.problems:
            print(f"safeextract: {name + ': ' if name else ''}{reason}", file=sys.stderr)
        print(f"safeextract: refused {args.archive}, nothing extracted", file=sys.stderr)
        return 1
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as err:
        print(f"safeextract: {err}", file=sys.stderr)
        return 2
    print(f"extracted {count} members ({total} bytes) to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
