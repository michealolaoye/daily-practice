# safeextract.py

Extracts a tar (plain, gz, bz2 or xz) or zip archive into a new directory,
all or nothing, and refuses archives that try to write anywhere else. Pure
stdlib.

## Why

Deploys unpack archives all the time: release tarballs, build artefacts,
uploaded bundles. `tar -xf` and `ZipFile.extractall` trust the archive
more than they should. A member called `../../.bashrc`, a symlink to
`/etc` followed by a file "inside" it, a device file or a 4 GB file of
zeros squeezed into 50 KB are all valid archive contents. Python 3.12+ has
`tarfile`'s `data` filter, but nothing equivalent for zip, and both
`extractall`s leave a half-written tree behind when something fails part
way. I wanted one command that checks everything first, then extracts
somewhere that only becomes the real directory once every member has made
it.

## API

```python
from safeextract import extract, open_archive, read_members, check, UnsafeArchive

try:
    count, size = extract("release.tar.gz", "/srv/app/releases/42")   # DEST must not exist
except UnsafeArchive as err:
    for name, reason in err.problems:
        print(name, reason)

kind, archive = open_archive("bundle.zip")                 # ("zip", ZipFile) or ("tar", TarFile)
with archive:
    problems = check(read_members(kind, archive), allow_links=False,
                     max_files=10_000, max_size=1 << 30)    # [] if it's safe
```

`extract` raises `UnsafeArchive` (with every problem, not just the first),
`FileExistsError` if DEST exists, `ValueError` for a file that isn't an
archive, and `OSError` for I/O failures. In every case DEST is not created
and the temporary directory is removed.

## CLI usage

```
safeextract.py [--check] [--links] [--max-size SIZE] [--max-files N] ARCHIVE [DEST]

  --check          only report problems; DEST not needed
  --links          allow symlinks and hard links that stay inside DEST
  --max-size SIZE  limit on uncompressed bytes, e.g. 200M (default: 1G)
  --max-files N    limit on members (default: 10000)
```

## Real example

Three archives built for this in a scratch directory: a clean release
tarball, one with the usual tricks in it, and a zip whose entries clash.

```
$ python safeextract.py --check release.tar.gz
release.tar.gz: 5 members, nothing unsafe
$ python safeextract.py release.tar.gz out
extracted 5 members (55 bytes) to out
$ find out | sort
out
out/app
out/app/run.sh
out/app/server.py
out/README.md

$ python safeextract.py evil.tar out2; echo "exit $?"
safeextract: ../../.bashrc: '..' in path
safeextract: /etc/cron.d/x: absolute path
safeextract: logs: symlink (allow with --links)
safeextract: dev/sda: device, FIFO or other special file
safeextract: aux.txt: reserved name on Windows: aux.txt
safeextract: logs/app.log: inside symlink logs
safeextract: refused evil.tar, nothing extracted
exit 1

$ python safeextract.py --check dupes.zip; echo "exit $?"
safeextract: Config.yml: differs only in case from config.yml
safeextract: ../../boot.ini: '..' in path
safeextract: refused dupes.zip, nothing extracted
exit 1

$ python safeextract.py --max-size 40 release.tar.gz out3; echo "exit $?"
safeextract: 55 bytes uncompressed, more than the limit of 40
safeextract: refused release.tar.gz, nothing extracted
exit 1
```

`out2` and `out3` were never created.

## Exit codes

- `0` - extracted (or `--check` found nothing unsafe).
- `1` - the archive was refused; every problem is listed on stderr.
- `2` - usage error, DEST already exists, the file isn't a tar or zip
  archive, or an I/O error.

## Design notes

- **Check everything, then write.** All members are checked before the
  first byte is written, and every problem is reported, so one run tells
  you everything that's wrong with an archive.
- **Names.** Refused: absolute paths, drive letters (including the
  drive-relative `c:x`), `..`, backslashes, control characters and NUL,
  `:` (an NTFS alternate stream), names ending in a dot or space, and
  Windows device names (`con`, `aux.txt`, `lpt1`...). Those last few are
  harmless on Linux, but an archive that only extracts properly on one OS
  is a bug I'd rather hear about. `./a//b/` is just `a/b`. `tarfile`'s
  `data` filter quietly turns `/abs` into `abs`; this refuses it.
- **No duplicates, not even by case.** `config.yml` and `Config.yml` are
  one file on Windows and macOS, so which one wins would depend on the
  machine. A directory listed twice is fine.
- **Nothing inside a non-directory.** A member under a file or a symlink
  in the same archive is refused, whatever order they come in. That's the
  classic "symlink to `/etc`, then `etc/cron.d/x`" trick, and refusing it
  outright beats trying to resolve it.
- **Links only with `--links`, and only inside.** Symlink targets must be
  relative and are walked one step at a time from the link's directory:
  going above DEST is refused, and so is stepping *through* another link
  in the archive (`s -> .` plus `x -> s/../y` looks like `y` but resolves
  outside). Hard links must name a regular file in the archive and are
  extracted as copies, so they can never link to something outside. Links
  are created after every file and directory.
- **Device files, FIFOs and encrypted zip entries** are always refused.
- **Sizes.** The declared total is checked up front (that catches a zip
  bomb before any work is done), and bytes are counted again while
  copying, because zip headers can lie. The writer stops as soon as the
  limit is passed.
- **Permissions.** Only the executable bit survives (files become 0755 or
  0644), so setuid, setgid and world-writable modes are dropped.
  Modification times are kept.
- **All or nothing.** Extraction goes to a fresh `.safeextract-*`
  directory next to DEST and is renamed into place at the end. Any error,
  including a full disk, removes it. DEST must not already exist, so
  nothing is ever overwritten, and files are opened with `xb` so an
  existing path is never written through.
- **Zip permissions.** Many zip writers (Python's own `writestr` among
  them) store permission bits with no file type, so only an explicit
  non-regular type makes an entry "special".

## Running the tests

```
python -m unittest tests/test_safeextract.py
```

28 tests: names (everything refused and the look-alikes that are fine),
duplicates and case clashes, members under files and links in either
order, link permission, symlink targets including the step-through-a-link
trick, hard links, size and count limits, and real archives: a tarball
round trip (contents, mtimes, and on POSIX the executable bit kept and
setuid dropped), every
compression plus zip, an unsafe archive leaving nothing behind, an
existing DEST, a zip bomb, a lying size header, a failure part way, hard
links as copies, symlinks inside zips, and non-archives. One test checks
that everything `tarfile`'s `data` filter refuses is refused here too.
Then the CLI and its exit codes. The test that creates a real symlink is
skipped where the account can't create one (Windows without Developer
Mode).
