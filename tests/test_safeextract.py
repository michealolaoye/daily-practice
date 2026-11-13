import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import safeextract  # noqa: E402
from safeextract import Member, UnsafeArchive, check, extract, split_name  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "safeextract.py")


def _can_symlink():
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink("x", os.path.join(d, "probe"))
        except (OSError, NotImplementedError):
            return False
        return True


CAN_SYMLINK = _can_symlink()


def tar_info(name, kind="file", data=b"", link="", mode=0o644, mtime=1_700_000_000):
    info = tarfile.TarInfo(name)
    info.type = {"file": tarfile.REGTYPE, "dir": tarfile.DIRTYPE, "symlink": tarfile.SYMTYPE,
                 "hardlink": tarfile.LNKTYPE, "block": tarfile.BLKTYPE, "char": tarfile.CHRTYPE,
                 "fifo": tarfile.FIFOTYPE}[kind]
    info.linkname = link
    info.mode = mode
    info.mtime = mtime
    info.size = len(data) if kind == "file" else 0
    return info


def write_tar(path, entries, mode="w"):
    """entries: (name, kind, data_or_link[, mode])"""
    with tarfile.open(path, mode) as tf:
        for entry in entries:
            name, kind, payload = entry[:3]
            extra = {"mode": entry[3]} if len(entry) > 3 else {}
            if kind == "file":
                tf.addfile(tar_info(name, kind, data=payload, **extra), io.BytesIO(payload))
            else:
                tf.addfile(tar_info(name, kind, link=payload, **extra))
    return path


def write_zip(path, entries, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def members(*specs):
    """Member objects without an archive: (name, kind[, link][, size])."""
    out = []
    for spec in specs:
        name, kind = spec[:2]
        link = spec[2] if len(spec) > 2 else None
        size = spec[3] if len(spec) > 3 else 0
        out.append(Member(name, kind, size=size, link=link))
    return out


def reasons(problems):
    return {name: reason for name, reason in problems}


def tree(root):
    """{relative posix path: bytes} for every file under root."""
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root).replace(os.sep, "/")] = fh.read()
    return out


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def leftovers(self):
        return [n for n in os.listdir(self.dir) if n.startswith(".safeextract-")]


class SplitNameTest(unittest.TestCase):
    def test_normalises_harmless_spellings(self):
        self.assertEqual(split_name("./a//b/"), ("a", "b"))
        self.assertEqual(split_name("a/./b"), ("a", "b"))
        self.assertEqual(split_name("."), ())
        self.assertEqual(split_name("café/日本.txt"), ("café", "日本.txt"))

    def test_refuses_what_could_land_elsewhere(self):
        cases = {
            "/etc/passwd": "absolute", "C:/Windows/x": "absolute", "c:x": "absolute",
            "a/../b": "'..'", "..": "'..'", "x/..": "'..'",
            "a\\b": "backslash", "..\\x": "backslash",
            "a:b": "absolute",  # drive-relative on Windows
            "ab:c": "':'", "file.txt:stream": "':'",
            "a/con": "reserved", "CON.txt": "reserved", "lpt1": "reserved", "Aux.tar.gz": "reserved",
            "x.": "dot or space", "dir /f": "dot or space", "...": "dot or space",
            "": "empty", "a\x00b": "NUL", "a\nb": "control", "a\x1bb": "control",
        }
        for name, why in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as ctx:
                    split_name(name)
                self.assertIn(why, str(ctx.exception))

    def test_lookalikes_that_are_fine(self):
        for name in ("con2", "console.txt", "a..b", "..a", "x/nul2.txt", "com10"):
            with self.subTest(name=name):
                self.assertTrue(split_name(name))


class CheckTest(unittest.TestCase):
    def test_clean_list(self):
        ms = members(("./", "dir"), ("app/", "dir"), ("app/x.py", "file"), ("README", "file"))
        self.assertEqual(check(ms), [])
        self.assertEqual([m.parts for m in ms], [(), ("app",), ("app", "x.py"), ("README",)])

    def test_duplicates_and_case_collisions(self):
        problems = reasons(check(members(("a.txt", "file"), ("./a.txt", "file"), ("B.txt", "file"),
                                         ("b.TXT", "file"), ("d/", "dir"), ("d", "dir"))))
        self.assertEqual(problems["./a.txt"], "appears twice")
        self.assertEqual(problems["b.TXT"], "differs only in case from B.txt")
        self.assertNotIn("d", problems)  # a directory listed twice is harmless

    def test_nothing_inside_a_file_or_link_in_either_order(self):
        for order in (1, -1):
            with self.subTest(order=order):
                ms = members(("x", "file"), ("x/y", "file"), ("l", "symlink", "real"), ("l/z", "file"),
                             ("real/", "dir"))[::order]
                problems = reasons(check(ms, allow_links=True))
                self.assertEqual(problems["x/y"], "inside file x")
                self.assertEqual(problems["l/z"], "inside symlink l")
                self.assertNotIn("l", problems)

    def test_links_need_permission_and_special_files_never_pass(self):
        ms = members(("l", "symlink", "a"), ("h", "hardlink", "a"), ("a", "file"), ("dev", "special"),
                     ("secret", "encrypted"))
        problems = reasons(check(ms))
        self.assertEqual(problems["l"], "symlink (allow with --links)")
        self.assertEqual(problems["h"], "hardlink (allow with --links)")
        self.assertEqual(problems["dev"], "device, FIFO or other special file")
        self.assertEqual(problems["secret"], "encrypted")
        self.assertEqual(set(reasons(check(ms, allow_links=True))), {"dev", "secret"})

    def test_symlink_targets(self):
        ok = members(("a/b/l1", "symlink", "../c"), ("a/l2", "symlink", "b/../b"), ("l3", "symlink", "."),
                     ("chain1", "symlink", "chain2"), ("chain2", "symlink", "a"), ("a/b/", "dir"))
        self.assertEqual(check(ok, allow_links=True), [])
        bad = {
            "up": ("..", "outside"), "deep/up": ("../../x", "outside"), "abs": ("/etc", "absolute"),
            "drive": ("C:/x", "absolute"), "bs": ("..\\x", "absolute"), "empty": ("", "no target"),
            "trick": ("s/../x", "through another link"),
        }
        ms = members(("s", "symlink", "."), *[(name, "symlink", link) for name, (link, _) in bad.items()])
        problems = reasons(check(ms, allow_links=True))
        self.assertNotIn("s", problems)
        for name, (_, why) in bad.items():
            with self.subTest(name=name):
                self.assertIn(why, problems[name])

    def test_hard_links_must_name_a_file_in_the_archive(self):
        ms = members(("f", "file"), ("d/", "dir"), ("s", "symlink", "f"), ("ok", "hardlink", "./F"),
                     ("missing", "hardlink", "nope"), ("todir", "hardlink", "d"), ("tolink", "hardlink", "s"),
                     ("out", "hardlink", "../etc/passwd"), ("abs", "hardlink", "/etc/passwd"))
        problems = reasons(check(ms, allow_links=True))
        self.assertNotIn("ok", problems)
        self.assertEqual(ms[3].target, ("f",))
        for name in ("missing", "todir", "tolink"):
            self.assertIn("isn't a file in the archive", problems[name])
        self.assertIn("'..'", problems["out"])
        self.assertIn("absolute", problems["abs"])

    def test_limits_on_count_and_declared_size(self):
        ms = members(("a", "file", None, 600), ("b", "file", None, 500), ("d/", "dir"))
        self.assertEqual(check(ms, max_files=3, max_size=1100), [])
        problems = check(ms, max_files=2, max_size=1099)
        self.assertEqual(problems, [("", "3 members, more than the limit of 2"),
                                    ("", "1100 bytes uncompressed, more than the limit of 1099")])

    def test_a_file_cant_be_the_destination_itself(self):
        self.assertEqual(reasons(check(members(("./", "file")))), {"./": "names the destination itself"})


class ExtractTest(TempDirTest):
    def test_tarball_round_trip(self):
        arc = write_tar(self.path("r.tar.gz"), [
            ("./", "dir", ""), ("./app/", "dir", ""), ("./app/server.py", "file", b"print('hi')\n"),
            ("./app/run.sh", "file", b"#!/bin/sh\n", 0o4755), ("./notes/日本.txt", "file", "café".encode()),
        ], mode="w:gz")
        count, total = extract(arc, self.path("out"))
        self.assertEqual((count, total), (5, 27))  # "café" is 5 bytes
        self.assertEqual(tree(self.path("out")), {"app/server.py": b"print('hi')\n", "app/run.sh": b"#!/bin/sh\n",
                                                  "notes/日本.txt": "café".encode()})
        self.assertEqual(os.path.getmtime(self.path("out", "app", "server.py")), 1_700_000_000)
        if os.name == "posix":
            # Executable bit kept, setuid dropped.
            self.assertEqual(os.stat(self.path("out", "app", "run.sh")).st_mode & 0o7777, 0o755)
            self.assertEqual(os.stat(self.path("out", "app", "server.py")).st_mode & 0o7777, 0o644)
        self.assertEqual(self.leftovers(), [])

    def test_every_compression_and_zip(self):
        for mode, ext in (("w", "tar"), ("w:gz", "tgz"), ("w:bz2", "tar.bz2"), ("w:xz", "tar.xz")):
            with self.subTest(mode=mode):
                arc = write_tar(self.path("a." + ext), [("x/y.txt", "file", b"hello")], mode=mode)
                extract(arc, self.path("out-" + ext))
                self.assertEqual(tree(self.path("out-" + ext)), {"x/y.txt": b"hello"})
        arc = write_zip(self.path("a.zip"), [("deep/er/f.txt", b"zip"), ("empty/", b"")])
        extract(arc, self.path("out-zip"))
        self.assertEqual(tree(self.path("out-zip")), {"deep/er/f.txt": b"zip"})
        self.assertTrue(os.path.isdir(self.path("out-zip", "empty")))

    def test_unsafe_archive_writes_nothing(self):
        arc = write_tar(self.path("evil.tar"), [
            ("ok.txt", "file", b"fine"), ("../../.bashrc", "file", b"x"), ("/etc/cron.d/x", "file", b"x"),
            ("logs", "symlink", "/var/log"), ("logs/app.log", "file", b"x"), ("dev/sda", "block", ""),
        ])
        with self.assertRaises(UnsafeArchive) as ctx:
            extract(arc, self.path("out"))
        self.assertEqual(set(reasons(ctx.exception.problems)),
                         {"../../.bashrc", "/etc/cron.d/x", "logs", "logs/app.log", "dev/sda"})
        self.assertFalse(os.path.lexists(self.path("out")))
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(sorted(os.listdir(self.dir)), ["evil.tar"])

    def test_destination_must_not_exist(self):
        arc = write_tar(self.path("a.tar"), [("f", "file", b"new")])
        os.mkdir(self.path("out"))
        with open(self.path("out", "keep"), "wb") as fh:
            fh.write(b"old")
        with self.assertRaises(FileExistsError):
            extract(arc, self.path("out"))
        self.assertEqual(tree(self.path("out")), {"keep": b"old"})

    def test_zip_bomb_refused_on_declared_size(self):
        arc = write_zip(self.path("bomb.zip"), [(f"z{i}", b"\0" * (1 << 20)) for i in range(8)])
        self.assertLess(os.path.getsize(arc), 100_000)
        with self.assertRaises(UnsafeArchive) as ctx:
            extract(arc, self.path("out"), max_size=4 << 20)
        self.assertIn("8388608 bytes uncompressed", ctx.exception.problems[0][1])
        self.assertFalse(os.path.lexists(self.path("out")))

    def test_size_limit_enforced_on_bytes_written_too(self):
        # As if the headers had under-reported the sizes: the check passes, so
        # the copy itself has to stop.
        arc = write_zip(self.path("liar.zip"), [("a", b"x" * 3000), ("b", b"y" * 3000)])
        real = safeextract.check

        def lenient(ms, **kw):
            return real(ms, **dict(kw, max_size=10**9))

        with mock.patch.object(safeextract, "check", lenient):
            with self.assertRaises(UnsafeArchive) as ctx:
                extract(arc, self.path("out"), max_size=5000)
        self.assertIn("larger than its headers say", ctx.exception.problems[0][1])
        self.assertFalse(os.path.lexists(self.path("out")))
        self.assertEqual(self.leftovers(), [])

    def test_failure_part_way_cleans_up(self):
        arc = write_tar(self.path("a.tar"), [("one", "file", b"1"), ("two", "file", b"2")])
        real = safeextract._write_file
        calls = []

        def fail_second(src, path, budget):
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(src, path, budget)

        with mock.patch.object(safeextract, "_write_file", fail_second):
            with self.assertRaises(OSError):
                extract(arc, self.path("out"))
        self.assertEqual(len(calls), 2)
        self.assertFalse(os.path.lexists(self.path("out")))
        self.assertEqual(self.leftovers(), [])

    def test_hard_link_becomes_a_copy(self):
        arc = write_tar(self.path("h.tar"), [("bin/tool", "file", b"#!x", 0o755), ("bin/alias", "hardlink", "bin/tool")])
        with self.assertRaises(UnsafeArchive):
            extract(arc, self.path("no"))
        extract(arc, self.path("out"), allow_links=True)
        self.assertEqual(tree(self.path("out")), {"bin/tool": b"#!x", "bin/alias": b"#!x"})
        os.chmod(self.path("out", "bin", "tool"), 0o644)
        with open(self.path("out", "bin", "tool"), "wb") as fh:
            fh.write(b"changed")
        self.assertEqual(tree(self.path("out"))["bin/alias"], b"#!x")

    @unittest.skipUnless(CAN_SYMLINK, "this account can't create symlinks")
    def test_symlink_inside_is_created_with_links(self):
        arc = write_tar(self.path("s.tar"), [("real/f", "file", b"data"), ("alias", "symlink", "real/f")])
        extract(arc, self.path("out"), allow_links=True)
        self.assertEqual(os.readlink(self.path("out", "alias")), "real/f")
        with open(self.path("out", "alias"), "rb") as fh:
            self.assertEqual(fh.read(), b"data")

    def test_symlinks_in_zips_are_recognised(self):
        arc = self.path("l.zip")
        with zipfile.ZipFile(arc, "w") as zf:
            info = zipfile.ZipInfo("etc")
            info.external_attr = (0o120777) << 16
            zf.writestr(info, "/etc")
            zf.writestr("plain.txt", "p")
        with self.assertRaises(UnsafeArchive) as ctx:
            extract(arc, self.path("out"), allow_links=True)
        self.assertEqual(reasons(ctx.exception.problems), {"etc": "link to an absolute path: /etc"})

    def test_not_an_archive(self):
        with open(self.path("junk.bin"), "wb") as fh:
            fh.write(b"this is not an archive at all" * 50)
        with self.assertRaises(ValueError):
            extract(self.path("junk.bin"), self.path("out"))
        self.assertFalse(os.path.lexists(self.path("out")))


class AgreesWithTarfileDataFilterTest(TempDirTest):
    """Everything Python's own `data` extraction filter refuses, this refuses
    too. It refuses more: Windows names, case clashes, links without
    --links, and absolute names, which tarfile quietly makes relative."""

    def test_absolute_names_differ_on_purpose(self):
        self.assertEqual(tarfile.data_filter(tar_info("/abs"), self.path("dest")).name, "abs")
        self.assertIn("absolute", reasons(check(members(("/abs", "file"))))["/abs"])

    def test_cross_check(self):
        dest = self.path("dest")
        evil = [
            tar_info("../x"), tar_info("a/../../x"),
            tar_info("l1", "symlink", link="/etc"), tar_info("l2", "symlink", link="../../x"),
            tar_info("sub/l3", "symlink", link="../../x"), tar_info("h1", "hardlink", link="/etc/passwd"),
            tar_info("h2", "hardlink", link="../outside"), tar_info("b", "block"), tar_info("c", "char"),
            tar_info("f", "fifo"),
        ]
        for info in evil:
            with self.subTest(name=info.name):
                with self.assertRaises(tarfile.FilterError):
                    tarfile.data_filter(info, dest)
                ms = safeextract._tar_members(_FakeTar([info]))
                self.assertTrue(check(ms, allow_links=True), "tarfile refuses it but safeextract didn't")


class _FakeTar:
    def __init__(self, infos):
        self.infos = infos

    def getmembers(self):
        return self.infos


class CliTest(TempDirTest):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, encoding="utf-8",
                           env=env, cwd=self.dir)
        return r.returncode, r.stdout.replace("\r\n", "\n"), r.stderr.replace("\r\n", "\n")

    def test_extract_and_check(self):
        write_tar(self.path("r.tgz"), [("a/b.txt", "file", b"hi"), ("c", "file", b"!!")], mode="w:gz")
        self.assertEqual(self.run_cli("--check", "r.tgz"), (0, "r.tgz: 2 members, nothing unsafe\n", ""))
        self.assertEqual(self.run_cli("r.tgz", "out"), (0, "extracted 2 members (4 bytes) to out\n", ""))
        self.assertEqual(tree(self.path("out")), {"a/b.txt": b"hi", "c": b"!!"})

    def test_refusal_lists_every_problem(self):
        write_tar(self.path("e.tar"), [("../x", "file", b""), ("aux.txt", "file", b""), ("ok", "file", b"")])
        code, out, err = self.run_cli("e.tar", "out")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "safeextract: ../x: '..' in path\n"
                              "safeextract: aux.txt: reserved name on Windows: aux.txt\n"
                              "safeextract: refused e.tar, nothing extracted\n")
        self.assertFalse(os.path.lexists(self.path("out")))
        self.assertEqual(self.run_cli("--check", "e.tar")[0], 1)

    def test_limits_from_the_command_line(self):
        write_zip(self.path("z.zip"), [("a", b"x" * 3000)])
        code, _, err = self.run_cli("--max-size", "2K", "z.zip", "out")
        self.assertEqual(code, 1)
        self.assertIn("3000 bytes uncompressed, more than the limit of 2048", err)
        self.assertEqual(self.run_cli("--max-size", "1M", "--max-files", "1", "z.zip", "out")[0], 0)
        code, _, err = self.run_cli("--max-size", "lots", "z.zip", "out2")
        self.assertEqual(code, 2)
        self.assertIn("not a size", err)

    def test_usage_and_io_errors_exit_2(self):
        write_tar(self.path("a.tar"), [("f", "file", b"")])
        code, _, err = self.run_cli("a.tar")
        self.assertEqual(code, 2)
        self.assertIn("DEST is required", err)
        os.mkdir(self.path("taken"))
        code, _, err = self.run_cli("a.tar", "taken")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        code, _, err = self.run_cli("missing.tar", "out")
        self.assertEqual(code, 2)
        with open(self.path("junk"), "wb") as fh:
            fh.write(b"\x00junk" * 200)
        code, _, err = self.run_cli("junk", "out")
        self.assertEqual(code, 2)
        self.assertIn("not a tar or zip archive", err)


if __name__ == "__main__":
    unittest.main()
