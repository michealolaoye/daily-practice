import io
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import applypatch  # noqa: E402
import udiff  # noqa: E402
from udiff import NO_NEWLINE, edit_script, hunks, split_lines, unified_diff  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "udiff.py")
HAVE_GIT = shutil.which("git") is not None


def lcs_len(a, b):
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            d[i][j] = d[i + 1][j + 1] + 1 if a[i] == b[j] else max(d[i + 1][j], d[i][j + 1])
    return d[0][0]


def random_lines(rng, alphabet="abcd", max_len=25, ragged_end=False):
    lines = [rng.choice(alphabet) + "\n" for _ in range(rng.randrange(0, max_len))]
    if ragged_end and lines and rng.random() < 0.3:
        lines[-1] = lines[-1].rstrip("\n")  # no newline at end of file
    return lines


def write(path, text):
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return f.read()


def run(args):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")


class SplitLines(unittest.TestCase):
    def test_keeps_endings_and_the_ragged_last_line(self):
        self.assertEqual(split_lines(""), [])
        self.assertEqual(split_lines("a"), ["a"])
        self.assertEqual(split_lines("a\n"), ["a\n"])
        self.assertEqual(split_lines("a\n\nb"), ["a\n", "\n", "b"])
        self.assertEqual(split_lines("a\r\nb\r\n"), ["a\r\n", "b\r\n"])
        self.assertEqual(split_lines("a\rb\n"), ["a\rb\n"])  # a bare CR is not a line break


class EditScript(unittest.TestCase):
    def test_reconstructs_b_and_is_minimal_on_random_input(self):
        rng = random.Random(1)
        for _ in range(400):
            a, b = random_lines(rng), random_lines(rng, alphabet=rng.choice(["ab", "abcd", "abcdefgh"]))
            ops, exact = edit_script(a, b)
            self.assertTrue(exact)
            rebuilt, taken_a = [], []
            for tag, i, j in ops:
                if tag == "=":
                    self.assertEqual(a[i], b[j])
                    rebuilt.append(a[i])
                    taken_a.append(i)
                elif tag == "+":
                    rebuilt.append(b[j])
                else:
                    taken_a.append(i)
            self.assertEqual(rebuilt, b)
            self.assertEqual(taken_a, list(range(len(a))))  # every line of a is kept or deleted, in order
            edits = sum(1 for op in ops if op[0] != "=")
            self.assertEqual(edits, len(a) + len(b) - 2 * lcs_len(a, b))  # shortest possible

    def test_identical_empty_and_disjoint(self):
        self.assertEqual(edit_script([], [])[0], [])
        self.assertEqual([o[0] for o in edit_script(["x\n"], ["x\n"])[0]], ["="])
        self.assertEqual([o[0] for o in edit_script([], ["x\n", "y\n"])[0]], ["+", "+"])
        self.assertEqual([o[0] for o in edit_script(["x\n", "y\n"], [])[0]], ["-", "-"])
        self.assertEqual(sum(1 for o in edit_script(["a\n"] * 5, ["b\n"] * 5)[0] if o[0] != "="), 10)

    def test_myers_paper_example(self):
        # the ABCABBA / CBABAC example from the paper: shortest script is 5 edits
        a, b = list("ABCABBA"), list("CBABAC")
        ops, _ = edit_script(a, b)
        self.assertEqual(sum(1 for o in ops if o[0] != "="), 5)
        self.assertEqual(lcs_len(a, b), 4)

    def test_falls_back_to_replacing_everything_when_too_different(self):
        a = [f"a{i}\n" for i in range(300)]
        b = [f"b{i}\n" for i in range(300)]
        ops, exact = edit_script(a, b, max_edits=50)
        self.assertFalse(exact)
        self.assertEqual([o[0] for o in ops], ["-"] * 300 + ["+"] * 300)
        # and the result is still a correct patch
        ops2, exact2 = edit_script(a, b, max_edits=10_000)
        self.assertTrue(exact2)

    def test_long_common_stretches_are_cheap(self):
        # tens of thousands of equal lines either side would be hopeless if searched
        big = ["same\n"] * 50_000
        ops, exact = edit_script(big + ["x\n"] + big, big + ["y\n"] + big)
        self.assertTrue(exact)
        self.assertEqual(sum(1 for o in ops if o[0] != "="), 2)


class Format(unittest.TestCase):
    def test_exact_text_for_a_simple_change(self):
        a = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n"
        b = "one\ntwo\nthree\nFOUR\nfive\nsix\nseven\neight\n"
        self.assertEqual(
            unified_diff(a, b, "a/f", "b/f"),
            "--- a/f\n+++ b/f\n@@ -1,7 +1,7 @@\n one\n two\n three\n-four\n+FOUR\n five\n six\n seven\n",
        )

    def test_identical_texts_give_an_empty_diff(self):
        self.assertEqual(unified_diff("a\nb\n", "a\nb\n"), "")
        self.assertEqual(unified_diff("", ""), "")

    def test_header_ranges_follow_diff_u_rules(self):
        # a one-line range omits ",1"; an empty range names the line before it
        self.assertEqual(udiff._range(4, 1), "5")
        self.assertEqual(udiff._range(4, 3), "5,3")
        self.assertEqual(udiff._range(4, 0), "4,0")
        self.assertEqual(udiff._range(0, 0), "0,0")
        d = unified_diff("", "new\n", "a", "b")
        self.assertIn("@@ -0,0 +1 @@", d)
        d = unified_diff("old\n", "", "a", "b")
        self.assertIn("@@ -1 +0,0 @@", d)

    def test_hunks_merge_only_when_context_overlaps(self):
        a = "".join(f"{i}\n" for i in range(30))
        lines = a.split("\n")[:-1]
        lines[5] = "five"
        lines[10] = "ten"  # gap of 4 lines between the two changes
        b = "\n".join(lines) + "\n"
        self.assertEqual(unified_diff(a, b, context=3).count("@@ -"), 1)
        self.assertEqual(unified_diff(a, b, context=2).count("@@ -"), 1)   # gap 4 <= 2*2: merged
        self.assertEqual(unified_diff(a, b, context=1).count("@@ -"), 2)   # gap 4 > 2*1: split
        self.assertEqual(unified_diff(a, b, context=0).count("@@ -"), 2)

    def test_no_newline_at_end_of_file_marker(self):
        d = unified_diff("a\nb", "a\nc\n", "a", "b")
        self.assertEqual(d, f"--- a\n+++ b\n@@ -1,2 +1,2 @@\n a\n-b\n{NO_NEWLINE}+c\n")
        d = unified_diff("a\n", "a", "a", "b")
        self.assertEqual(d, f"--- a\n+++ b\n@@ -1 +1 @@\n-a\n+a\n{NO_NEWLINE}")
        # both sides lack it and the last line is the same: no change at all
        self.assertEqual(unified_diff("a\nb", "a\nb"), "")

    def test_crlf_lines_are_compared_exactly(self):
        d = unified_diff("a\r\nb\r\n", "a\nb\r\n", "a", "b")
        self.assertEqual(d, "--- a\n+++ b\n@@ -1,2 +1,2 @@\n-a\r\n+a\n b\r\n")
        self.assertEqual(unified_diff("a\r\n", "a\r\n"), "")

    def test_negative_context_is_refused(self):
        with self.assertRaises(ValueError):
            unified_diff("a\n", "b\n", context=-1)


class Applies(unittest.TestCase):
    """The proof of a diff is that real patch tools accept it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def cases(self, count, seed):
        rng = random.Random(seed)
        for _ in range(count):
            a = random_lines(rng, ragged_end=True)
            b = random_lines(rng, alphabet=rng.choice(["ab", "abcd"]), ragged_end=True)
            yield "".join(a), "".join(b), rng.choice([0, 1, 3, 8])

    def test_applypatch_py_reproduces_b(self):
        n = 0
        for a, b, ctx in self.cases(300, 2):
            patch = unified_diff(a, b, "a/f", "b/f", ctx)
            if not patch:
                continue
            n += 1
            write(os.path.join(self.tmp, "f"), a)
            applypatch.apply_patch(patch, root=self.tmp, strip=1)
            self.assertEqual(read(os.path.join(self.tmp, "f")), b, (a, b, ctx))
        self.assertGreater(n, 250)

    def test_reverse_patch_restores_a(self):
        for a, b, ctx in self.cases(100, 3):
            patch = unified_diff(a, b, "a/f", "b/f", ctx)
            if not patch:
                continue
            write(os.path.join(self.tmp, "f"), b)
            applypatch.apply_patch(patch, root=self.tmp, strip=1, reverse=True)
            self.assertEqual(read(os.path.join(self.tmp, "f")), a)

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_git_apply_reproduces_b(self):
        n = 0
        for a, b, ctx in self.cases(40, 4):
            patch = unified_diff(a, b, "f", "f", ctx)
            if not patch:
                continue
            n += 1
            write(os.path.join(self.tmp, "f"), a)
            write(os.path.join(self.tmp, "p"), patch)
            cmd = ["git", "-c", "core.autocrlf=false", "apply"] + (["--unidiff-zero"] if ctx == 0 else []) + ["p"]
            r = subprocess.run(cmd, cwd=self.tmp, capture_output=True)
            self.assertEqual(r.returncode, 0, (a, b, ctx, r.stderr))
            self.assertEqual(read(os.path.join(self.tmp, "f")), b, (a, b, ctx))
        self.assertGreater(n, 30)

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_same_hunks_as_git_diff_when_there_is_only_one_way(self):
        pairs = [
            ("a\nb\nc\nd\ne\nf\ng\nh\n", "a\nb\nc\nX\ne\nf\ng\nh\n"),
            ("a\nb\n", "a\nb\nc\n"),
            ("x\n", ""),
            ("", "x\ny\n"),
            ("a\nb\nc", "a\nb\nd"),
            ("a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\n", "A\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nL\n"),
        ]
        for a, b in pairs:
            for ctx in (0, 1, 3):
                write(os.path.join(self.tmp, "a.txt"), a)
                write(os.path.join(self.tmp, "b.txt"), b)
                r = subprocess.run(
                    ["git", "-c", "core.autocrlf=false", "diff", "--no-index", "--no-color", f"-U{ctx}", "a.txt", "b.txt"],
                    cwd=self.tmp, capture_output=True,
                )
                theirs = r.stdout.decode().replace("\r\n", "\n")
                theirs = theirs[theirs.index("@@"):] if "@@" in theirs else ""
                # git appends the nearest "function" line after the header; diff -u doesn't
                theirs = re.sub(r"^(@@ [^@]+ @@).*$", r"\1", theirs, flags=re.M)
                ours = unified_diff(a, b, "a.txt", "b.txt", ctx)
                ours = ours[ours.index("@@"):] if ours else ""
                self.assertEqual(ours, theirs, (a, b, ctx))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name, text=None, data=None):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data if data is not None else text.encode("utf-8"))
        return p

    def test_exit_0_when_the_same_and_1_when_different(self):
        a = self.path("a", "x\ny\n")
        b = self.path("b", "x\ny\n")
        self.assertEqual(run([a, b]), (0, b"", ""))
        c = self.path("c", "x\nz\n")
        code, out, err = run([a, c])
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out.decode().replace("\r\n", "\n"),
                         f"--- {a}\n+++ {c}\n@@ -1,2 +1,2 @@\n x\n-y\n+z\n")

    def test_context_option(self):
        a = self.path("a", "".join(f"{i}\n" for i in range(20)))
        b = self.path("b", "".join(f"{i}\n" if i != 10 else "X\n" for i in range(20)))
        out = run(["-U", "1", a, b])[1].decode().replace("\r\n", "\n")
        self.assertIn("@@ -10,3 +10,3 @@", out)
        out = run(["-U0", a, b])[1].decode().replace("\r\n", "\n")
        self.assertIn("@@ -11 +11 @@", out)

    def test_non_utf8_bytes_survive_the_round_trip(self):
        a = self.path("a", data=b"caf\xe9\nsame\n")
        b = self.path("b", data=b"caf\xe8\nsame\n")
        code, out, _ = run([a, b])
        self.assertEqual(code, 1)
        self.assertIn(b"-caf\xe9", out)
        self.assertIn(b"+caf\xe8", out)

    def test_binary_files_are_reported_not_diffed(self):
        a = self.path("a", data=b"\x00\x01\x02")
        b = self.path("b", data=b"\x00\x01\x03")
        code, out, _ = run([a, b])
        self.assertEqual(code, 1)
        self.assertIn(b"Binary files", out)
        self.assertNotIn(b"@@", out)

    def test_errors_exit_2(self):
        a = self.path("a", "x\n")
        code, _, err = run([a, os.path.join(self.tmp, "missing")])
        self.assertEqual(code, 2)
        self.assertIn("udiff:", err)
        self.assertEqual(run(["-U", "-1", a, a])[0], 2)

    def test_main_with_a_text_stream(self):
        a = self.path("a", "1\n")
        b = self.path("b", "2\n")
        out = io.StringIO()
        self.assertEqual(udiff.main([a, b], stdout=out), 1)
        self.assertIn("-1\n+2\n", out.getvalue())

    def test_max_edits_option_falls_back_but_still_applies(self):
        a_text = "".join(f"a{i}\n" for i in range(100))
        b_text = "".join(f"b{i}\n" for i in range(100))
        a, b = self.path("a", a_text), self.path("b", b_text)
        out = run(["--max-edits", "10", a, b])[1].decode().replace("\r\n", "\n")
        self.assertIn("@@ -1,100 +1,100 @@", out)
        self.assertEqual(out.count("\n-a"), 100)


if __name__ == "__main__":
    unittest.main()
