import difflib
import os
import random
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import applypatch  # noqa: E402
from applypatch import HunkFailed, MalformedPatch, apply_hunks, apply_patch, parse_patch  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "applypatch.py")


def diff(old, new, name="f.txt"):
    """A `diff -u` style patch (a/ and b/ prefixes) turning `old` into `new`."""
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}"))


def hunks_of(patch_text):
    return parse_patch(patch_text)[0].hunks


def lines(n):
    return "".join(f"line {i}\n" for i in range(1, n + 1))


class TempTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def path(self, name):
        return os.path.join(self.root, *name.split("/"))

    def write(self, name, text):
        full = self.path(name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def read(self, name):
        with open(self.path(name), encoding="utf-8", newline="") as fh:
            return fh.read()

    def listing(self):
        found = []
        for base, _dirs, files in os.walk(self.root):
            for f in files:
                found.append(os.path.relpath(os.path.join(base, f), self.root).replace(os.sep, "/"))
        return sorted(found)


class ParsePatchTest(unittest.TestCase):
    def test_single_file_single_hunk(self):
        patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n"
        (fp,) = parse_patch(patch)
        self.assertEqual((fp.old_path, fp.new_path), ("a/f.txt", "b/f.txt"))
        (h,) = fp.hunks
        self.assertEqual((h.old_start, h.new_start), (1, 1))
        self.assertEqual(h.lines, [(" ", "one"), ("-", "two"), ("+", "TWO")])

    def test_several_files_and_hunks(self):
        patch = (
            "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n@@ -10 +10 @@\n-c\n+d\n"
            "--- a/y\n+++ b/y\n@@ -1 +1 @@\n-e\n+f\n"
        )
        patches = parse_patch(patch)
        self.assertEqual([len(p.hunks) for p in patches], [2, 1])

    def test_count_defaults_to_one(self):
        (h,) = hunks_of("--- a/x\n+++ b/x\n@@ -3 +3 @@\n-a\n+b\n")
        self.assertEqual(len(h.old()), 1)
        self.assertEqual(len(h.new()), 1)

    def test_git_headers_and_prose_are_ignored(self):
        patch = (
            "From: someone\nSubject: [PATCH] thing\n\nSome words.\n\n"
            "diff --git a/f b/f\nindex 123..456 100644\n"
            "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n-- \n2.40.0\n"
        )
        (fp,) = parse_patch(patch)
        self.assertEqual(fp.hunks[0].lines, [("-", "a"), ("+", "b")])

    def test_timestamp_after_a_tab_is_dropped(self):
        (fp,) = parse_patch("--- a/f\t2024-01-01 10:00:00\n+++ b/f\t2024-01-02\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual((fp.old_path, fp.new_path), ("a/f", "b/f"))

    def test_quoted_path(self):
        (fp,) = parse_patch('--- "a/my file"\n+++ "b/my file"\n@@ -1 +1 @@\n-a\n+b\n')
        self.assertEqual(fp.new_path, "b/my file")

    def test_crlf_patch_text(self):
        (fp,) = parse_patch("--- a/f\r\n+++ b/f\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n")
        self.assertEqual(fp.hunks[0].lines, [("-", "a"), ("+", "b")])

    def test_empty_line_is_empty_context(self):
        (h,) = hunks_of("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-c\n+d\n")
        self.assertEqual(h.lines, [(" ", "a"), (" ", ""), ("-", "c"), ("+", "d")])

    def test_no_newline_markers(self):
        (h,) = hunks_of("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n")
        self.assertTrue(h.old_noeol)
        self.assertFalse(h.new_noeol)
        (h,) = hunks_of("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n")
        self.assertFalse(h.old_noeol)
        self.assertTrue(h.new_noeol)
        (h,) = hunks_of("--- a/f\n+++ b/f\n@@ -1 +1 @@\n a\n\\ No newline at end of file\n")
        self.assertTrue(h.old_noeol and h.new_noeol)

    def test_no_diff_at_all(self):
        with self.assertRaises(MalformedPatch):
            parse_patch("just some text\n")
        with self.assertRaises(MalformedPatch):
            parse_patch("")

    def test_section_without_hunks(self):
        with self.assertRaisesRegex(MalformedPatch, "no hunks"):
            parse_patch("--- a/f\n+++ b/f\n")

    def test_hunk_shorter_than_header(self):
        with self.assertRaisesRegex(MalformedPatch, "shorter"):
            parse_patch("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n-b\n")

    def test_garbage_inside_a_hunk(self):
        with self.assertRaisesRegex(MalformedPatch, "unexpected"):
            parse_patch("--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n?b\n")

    def test_marker_with_nothing_before_it(self):
        with self.assertRaises(MalformedPatch):
            parse_patch("--- a/f\n+++ b/f\n@@ -0,0 +0,0 @@\n\\ No newline at end of file\n")


class ApplyHunksTest(unittest.TestCase):
    def apply(self, old, new, patch=None):
        return apply_hunks(old, hunks_of(patch if patch is not None else diff(old, new)))

    def test_replace_a_line(self):
        old, new = lines(5), lines(5).replace("line 3", "THREE")
        self.assertEqual(self.apply(old, new), (new, []))

    def test_insert_and_delete(self):
        old = lines(10)
        new = old.replace("line 4\n", "").replace("line 8\n", "line 8\nextra\n")
        self.assertEqual(self.apply(old, new)[0], new)

    def test_insert_at_the_very_start(self):
        old, new = "a\nb\n", "zero\na\nb\n"
        patch = "--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+zero\n"
        self.assertEqual(self.apply(old, new, patch)[0], new)

    def test_insert_after_the_last_line(self):
        patch = "--- a/f\n+++ b/f\n@@ -2,0 +3 @@\n+c\n"
        self.assertEqual(self.apply("a\nb\n", None, patch)[0], "a\nb\nc\n")

    def test_applies_when_the_file_has_shifted(self):
        old = lines(30)
        new = old.replace("line 20\n", "TWENTY\n")
        shifted = "pre 1\npre 2\npre 3\n" + old
        text, notes = self.apply(shifted, None, diff(old, new))
        self.assertEqual(text, "pre 1\npre 2\npre 3\n" + new)
        self.assertEqual(len(notes), 1)
        self.assertIn("offset +3", notes[0])

    def test_applies_when_the_file_shifted_upwards(self):
        old = lines(30)
        new = old.replace("line 20\n", "TWENTY\n")
        trimmed = "".join(old.splitlines(True)[4:])
        text, notes = self.apply(trimmed, None, diff(old, new))
        self.assertEqual(text, "".join(new.splitlines(True)[4:]))
        self.assertIn("offset -4", notes[0])

    def test_drift_carries_to_later_hunks(self):
        old = lines(60)
        new = old.replace("line 10\n", "TEN\n").replace("line 50\n", "FIFTY\n")
        text, notes = self.apply("x\ny\n" + old, None, diff(old, new))
        self.assertEqual(text, "x\ny\n" + new)
        self.assertEqual(len(notes), 2)

    def test_context_mismatch_fails(self):
        old = lines(10)
        patch = diff(old, old.replace("line 5", "FIVE"))
        with self.assertRaisesRegex(HunkFailed, r"hunk #1 .*does not apply"):
            apply_hunks(old.replace("line 4", "other"), hunks_of(patch))

    def test_removed_line_must_match(self):
        with self.assertRaises(HunkFailed):
            apply_hunks("a\nX\nc\n", hunks_of("--- a/f\n+++ b/f\n@@ -1,3 +1,2 @@\n a\n-b\n c\n"))

    def test_failure_names_the_hunk(self):
        old = lines(40)
        new = old.replace("line 5\n", "FIVE\n").replace("line 35\n", "THIRTY-FIVE\n")
        with self.assertRaisesRegex(HunkFailed, "hunk #2"):
            apply_hunks(old.replace("line 35", "changed"), hunks_of(diff(old, new)))

    def test_hunk_past_the_end_of_a_short_file(self):
        with self.assertRaises(HunkFailed):
            apply_hunks("a\n", hunks_of(diff(lines(10), lines(10).replace("line 9", "x"))))

    def test_hunks_may_not_overlap_or_reorder(self):
        h1 = "@@ -5 +5 @@\n-line 5\n+FIVE\n"
        h2 = "@@ -1 +1 @@\n-line 1\n+ONE\n"
        with self.assertRaises(HunkFailed):
            apply_hunks(lines(8), hunks_of("--- a/f\n+++ b/f\n" + h1 + h2))

    def test_the_same_context_elsewhere_does_not_fool_it_when_the_right_place_exists(self):
        old = "x\ny\nx\ny\nx\ny\n"
        patch = "--- a/f\n+++ b/f\n@@ -3,2 +3,2 @@\n x\n-y\n+Y\n"
        self.assertEqual(self.apply(old, None, patch)[0], "x\ny\nx\nY\nx\ny\n")

    def test_nearest_match_wins(self):
        old = "x\ny\n" * 4
        patch = "--- a/f\n+++ b/f\n@@ -5,2 +5,2 @@\n x\n-y\n+Y\n"
        self.assertEqual(self.apply(old, None, patch)[0], "x\ny\nx\ny\nx\nY\nx\ny\n")

    def test_crlf_files_stay_crlf(self):
        old = "a\r\nb\r\nc\r\n"
        text, _ = apply_hunks(old, hunks_of("--- a/f\n+++ b/f\n@@ -1,3 +1,4 @@\n a\n-b\n+B\n+B2\n c\n"))
        self.assertEqual(text, "a\r\nB\r\nB2\r\nc\r\n")

    def test_empty_file_gets_content(self):
        text, _ = apply_hunks("", hunks_of("--- /dev/null\n+++ b/f\n@@ -0,0 +1,2 @@\n+a\n+b\n"))
        self.assertEqual(text, "a\nb\n")

    def test_everything_removed_leaves_an_empty_file(self):
        text, _ = apply_hunks("a\nb\n", hunks_of("--- a/f\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"))
        self.assertEqual(text, "")


class NoNewlineTest(unittest.TestCase):
    MARK = "\\ No newline at end of file\n"

    def test_file_without_final_newline_keeps_it(self):
        # b is the unterminated last line on both sides, so there is no newline after it.
        patch = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n" + self.MARK
        text, _ = apply_hunks("a\nb", hunks_of(patch))
        self.assertEqual(text, "A\nb")

    def test_adding_a_final_newline(self):
        patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-b\n" + self.MARK + "+b\n"
        self.assertEqual(apply_hunks("b", hunks_of(patch))[0], "b\n")

    def test_removing_the_final_newline(self):
        patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-b\n+b\n" + self.MARK
        self.assertEqual(apply_hunks("b\n", hunks_of(patch))[0], "b")

    def test_new_last_line_without_newline(self):
        patch = "--- a/f\n+++ b/f\n@@ -1 +1,2 @@\n a\n+b\n" + self.MARK
        # The marker follows the "+" line, so only the new side lacks the newline.
        self.assertEqual(apply_hunks("a\n", hunks_of(patch))[0], "a\nb")

    def test_a_marker_the_file_disagrees_with_fails(self):
        patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-b\n" + self.MARK + "+c\n"
        with self.assertRaises(HunkFailed):
            apply_hunks("b\n", hunks_of(patch))

    def test_a_file_missing_its_newline_needs_the_marker(self):
        patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-b\n+c\n"
        with self.assertRaises(HunkFailed):
            apply_hunks("b", hunks_of(patch))

    def test_marker_on_a_middle_hunk_cannot_match_early_lines(self):
        old = "a\nb\na\nb"
        patch = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n" + self.MARK + "+B\n"
        # "a\nb" occurs twice, but only the last one is at the end of an unterminated file.
        self.assertEqual(apply_hunks(old, hunks_of(patch))[0], "a\nb\na\nB\n")


class RoundTripTest(unittest.TestCase):
    def test_difflib_patches_apply_to_random_edits(self):
        rng = random.Random(1234)
        for _ in range(60):
            old_lines = [f"w{rng.randrange(8)} {i}\n" for i in range(rng.randrange(0, 40))]
            new_lines = list(old_lines)
            for _ in range(rng.randrange(0, 6)):
                kind = rng.choice("rid")
                at = rng.randrange(0, len(new_lines) + 1)
                if kind == "i":
                    new_lines.insert(at, f"new {rng.randrange(100)}\n")
                elif new_lines and at < len(new_lines):
                    if kind == "d":
                        del new_lines[at]
                    else:
                        new_lines[at] = f"changed {rng.randrange(100)}\n"
            old, new = "".join(old_lines), "".join(new_lines)
            patch = diff(old, new)
            if not patch:
                continue
            text, _ = apply_hunks(old, hunks_of(patch))
            self.assertEqual(text, new, patch)
            back, _ = apply_hunks(new, [h.reversed() for h in hunks_of(patch)])
            self.assertEqual(back, old, patch)


class ApplyPatchTest(TempTree):
    def test_patches_a_file(self):
        self.write("f.txt", "a\nb\nc\n")
        messages = apply_patch(diff("a\nb\nc\n", "a\nB\nc\n"), self.root)
        self.assertEqual(self.read("f.txt"), "a\nB\nc\n")
        self.assertEqual(messages, ["patching file f.txt"])

    def test_strip_levels(self):
        self.write("src/f.txt", "a\n")
        apply_patch(diff("a\n", "b\n", "src/f.txt"), self.root, strip=1)
        self.assertEqual(self.read("src/f.txt"), "b\n")
        apply_patch("--- src/f.txt\n+++ src/f.txt\n@@ -1 +1 @@\n-b\n+c\n", self.root, strip=0)
        self.assertEqual(self.read("src/f.txt"), "c\n")
        apply_patch("--- x/y/src/f.txt\n+++ x/y/src/f.txt\n@@ -1 +1 @@\n-c\n+d\n", self.root, strip=2)
        self.assertEqual(self.read("src/f.txt"), "d\n")

    def test_cannot_strip_more_than_the_path_has(self):
        with self.assertRaisesRegex(MalformedPatch, "cannot strip"):
            apply_patch(diff("a\n", "b\n"), self.root, strip=3)

    def test_creates_a_file_in_a_new_directory(self):
        patch = "--- /dev/null\n+++ b/new/dir/f.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n"
        messages = apply_patch(patch, self.root)
        self.assertEqual(self.read("new/dir/f.txt"), "one\ntwo\n")
        self.assertEqual(messages, ["creating file new/dir/f.txt"])

    def test_create_refuses_to_overwrite(self):
        self.write("f.txt", "keep\n")
        with self.assertRaisesRegex(HunkFailed, "already exists"):
            apply_patch("--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1 @@\n+x\n", self.root)
        self.assertEqual(self.read("f.txt"), "keep\n")

    def test_deletes_a_file(self):
        self.write("f.txt", "a\nb\n")
        messages = apply_patch("--- a/f.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n", self.root)
        self.assertEqual(self.listing(), [])
        self.assertEqual(messages, ["removing file f.txt"])

    def test_delete_refuses_when_content_differs(self):
        self.write("f.txt", "a\nEXTRA\n")
        with self.assertRaises(HunkFailed):
            apply_patch("--- a/f.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n", self.root)
        self.assertEqual(self.read("f.txt"), "a\nEXTRA\n")

    def test_delete_refuses_when_the_file_has_more_than_the_patch_removes(self):
        self.write("f.txt", "a\nb\nc\n")
        with self.assertRaisesRegex(HunkFailed, "not empty"):
            apply_patch("--- a/f.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n", self.root)
        self.assertEqual(self.read("f.txt"), "a\nb\nc\n")

    def test_missing_file(self):
        with self.assertRaisesRegex(HunkFailed, "no such file"):
            apply_patch(diff("a\n", "b\n"), self.root)

    def test_all_or_nothing(self):
        self.write("one.txt", "a\n")
        self.write("two.txt", "unrelated\n")
        patch = diff("a\n", "A\n", "one.txt") + diff("b\n", "B\n", "two.txt")
        with self.assertRaises(HunkFailed):
            apply_patch(patch, self.root)
        self.assertEqual(self.read("one.txt"), "a\n")
        self.assertEqual(self.read("two.txt"), "unrelated\n")
        self.assertEqual(self.listing(), ["one.txt", "two.txt"])

    def test_several_files_and_a_creation(self):
        self.write("one.txt", "a\n")
        self.write("sub/two.txt", "b\n")
        patch = (
            diff("a\n", "A\n", "one.txt")
            + diff("b\n", "B\n", "sub/two.txt")
            + "--- /dev/null\n+++ b/three.txt\n@@ -0,0 +1 @@\n+c\n"
        )
        apply_patch(patch, self.root)
        self.assertEqual((self.read("one.txt"), self.read("sub/two.txt"), self.read("three.txt")), ("A\n", "B\n", "c\n"))

    def test_dry_run_changes_nothing_but_still_checks(self):
        self.write("f.txt", "a\n")
        messages = apply_patch(diff("a\n", "b\n"), self.root, dry_run=True)
        self.assertEqual(self.read("f.txt"), "a\n")
        self.assertEqual(messages, ["patching file f.txt"])
        with self.assertRaises(HunkFailed):
            apply_patch(diff("zzz\n", "b\n"), self.root, dry_run=True)

    def test_dry_run_does_not_create_files(self):
        apply_patch("--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1 @@\n+x\n", self.root, dry_run=True)
        self.assertEqual(self.listing(), [])

    def test_reverse_undoes_a_patch(self):
        original = lines(20)
        changed = original.replace("line 7\n", "SEVEN\nand a half\n").replace("line 15\n", "")
        self.write("f.txt", original)
        patch = diff(original, changed)
        apply_patch(patch, self.root)
        self.assertEqual(self.read("f.txt"), changed)
        apply_patch(patch, self.root, reverse=True)
        self.assertEqual(self.read("f.txt"), original)

    def test_reverse_of_a_creation_deletes_and_of_a_deletion_creates(self):
        create = "--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        apply_patch(create, self.root)
        self.assertEqual(self.read("f.txt"), "a\nb\n")
        apply_patch(create, self.root, reverse=True)
        self.assertEqual(self.listing(), [])
        delete = "--- a/g.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        apply_patch(delete, self.root, reverse=True)
        self.assertEqual(self.read("g.txt"), "x\n")

    def test_uses_the_old_name_when_it_exists_otherwise_the_new_one(self):
        self.write("old.txt", "a\n")
        apply_patch("--- a/old.txt\n+++ b/renamed.txt\n@@ -1 +1 @@\n-a\n+b\n", self.root)
        self.assertEqual(self.read("old.txt"), "b\n")
        self.write("renamed.txt", "a\n")
        os.unlink(self.path("old.txt"))
        apply_patch("--- a/old.txt\n+++ b/renamed.txt\n@@ -1 +1 @@\n-a\n+c\n", self.root)
        self.assertEqual(self.read("renamed.txt"), "c\n")

    def test_refuses_paths_outside_the_tree(self):
        for bad in ("../outside.txt", "a/../../outside.txt", "x/sub/../../../outside.txt"):
            with self.assertRaisesRegex(MalformedPatch, "outside the tree"):
                apply_patch(f"--- /dev/null\n+++ {bad}\n@@ -0,0 +1 @@\n+x\n", self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "..", "outside.txt")))

    def test_refuses_absolute_paths(self):
        for bad in ("/etc/evil", "C:/evil.txt", "C:\\evil.txt"):
            with self.assertRaises(MalformedPatch):
                apply_patch(f"--- /dev/null\n+++ {bad}\n@@ -0,0 +1 @@\n+x\n", self.root, strip=0)
        self.assertEqual(self.listing(), [])

    def test_the_same_file_twice_is_refused(self):
        self.write("f.txt", "a\n")
        patch = diff("a\n", "b\n") + diff("b\n", "c\n")
        with self.assertRaisesRegex(MalformedPatch, "more than once"):
            apply_patch(patch, self.root)
        self.assertEqual(self.read("f.txt"), "a\n")

    def test_offsets_are_reported(self):
        old = lines(30)
        self.write("f.txt", "extra\n" + old)
        messages = apply_patch(diff(old, old.replace("line 20\n", "X\n")), self.root)
        self.assertEqual(messages[0], "patching file f.txt")
        self.assertIn("offset +1", messages[1])

    def test_crlf_and_unicode_survive(self):
        self.write("f.txt", "日本語\r\nb\r\n")
        apply_patch("--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n 日本語\n-b\n+é 😀\n", self.root)
        self.assertEqual(self.read("f.txt"), "日本語\r\né 😀\r\n")

    def test_no_temporary_files_are_left(self):
        self.write("f.txt", "a\n")
        apply_patch(diff("a\n", "b\n"), self.root)
        self.assertEqual(self.listing(), ["f.txt"])

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_permissions_are_kept(self):
        self.write("f.sh", "a\n")
        os.chmod(self.path("f.sh"), 0o755)
        apply_patch(diff("a\n", "b\n", "f.sh"), self.root)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("f.sh")).st_mode), 0o755)

    def test_error_classes_share_a_base(self):
        self.assertTrue(issubclass(applypatch.HunkFailed, applypatch.PatchError))
        self.assertTrue(issubclass(applypatch.MalformedPatch, applypatch.PatchError))


class CliTest(TempTree):
    def run_cli(self, *args, stdin=None):
        proc = subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def patch_file(self, text):
        self.write("change.patch", text)
        return self.path("change.patch")

    def test_patch_file_argument(self):
        self.write("f.txt", "a\n")
        code, out, err = self.run_cli("-C", self.root, self.patch_file(diff("a\n", "b\n")))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.strip(), "patching file f.txt")
        self.assertEqual(self.read("f.txt"), "b\n")

    def test_patch_from_stdin(self):
        self.write("f.txt", "a\n")
        code, _, _ = self.run_cli("-C", self.root, stdin=diff("a\n", "b\n").encode("utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(self.read("f.txt"), "b\n")

    def test_dry_run_and_reverse(self):
        self.write("f.txt", "a\n")
        patch = self.patch_file(diff("a\n", "b\n"))
        self.assertEqual(self.run_cli("-C", self.root, "--dry-run", patch)[0], 0)
        self.assertEqual(self.read("f.txt"), "a\n")
        self.run_cli("-C", self.root, patch)
        self.assertEqual(self.run_cli("-C", self.root, "-R", patch)[0], 0)
        self.assertEqual(self.read("f.txt"), "a\n")

    def test_p_option(self):
        self.write("f.txt", "a\n")
        patch = self.patch_file("--- f.txt\n+++ f.txt\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual(self.run_cli("-C", self.root, "-p0", patch)[0], 0)
        self.assertEqual(self.read("f.txt"), "b\n")

    def test_failed_hunk_is_exit_1_and_nothing_changes(self):
        self.write("f.txt", "different\n")
        code, out, err = self.run_cli("-C", self.root, self.patch_file(diff("a\n", "b\n")))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("does not apply", err)
        self.assertIn("nothing was changed", err)
        self.assertEqual(self.read("f.txt"), "different\n")

    def test_malformed_patch_is_exit_3(self):
        code, _, err = self.run_cli("-C", self.root, self.patch_file("this is not a patch\n"))
        self.assertEqual(code, 3)
        self.assertIn("no unified diff", err)

    def test_unreadable_input_is_exit_2(self):
        code, _, err = self.run_cli("-C", self.root, self.path("missing.patch"))
        self.assertEqual(code, 2)
        self.assertIn("applypatch:", err)

    def test_invalid_utf8_patch_is_exit_2(self):
        code, _, err = self.run_cli("-C", self.root, stdin=b"--- a/f\n+++ b/f\n@@ -1 +1 @@\n-\xff\n+b\n")
        self.assertEqual(code, 2)
        self.assertIn("utf-8", err.lower())

    def test_invalid_utf8_target_file_is_exit_2(self):
        with open(self.path("f.txt"), "wb") as fh:
            fh.write(b"\xff\xfe\n")
        code, _, _ = self.run_cli("-C", self.root, self.patch_file(diff("a\n", "b\n")))
        self.assertEqual(code, 2)

    def test_missing_directory_is_exit_2_or_1(self):
        code, _, _ = self.run_cli("-C", self.path("nope"), self.patch_file(diff("a\n", "b\n")))
        self.assertIn(code, (1, 2))

    def test_negative_strip_is_a_usage_error(self):
        code, _, err = self.run_cli("-p", "-1", self.patch_file(diff("a\n", "b\n")))
        self.assertEqual(code, 2)
        self.assertIn("usage", err.lower())

    def test_unicode_output_does_not_crash(self):
        self.write("日本語.txt", "a\n")
        code, out, _ = self.run_cli("-C", self.root, "--dry-run", self.patch_file(diff("a\n", "b\n", "日本語.txt")))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
