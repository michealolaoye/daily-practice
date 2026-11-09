import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import ignorematch  # noqa: E402
from ignorematch import IgnoreMatcher, Tree, compile_pattern, parse  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "ignorematch.py")


def matcher(*lines, ignore_case=False):
    m = IgnoreMatcher(ignore_case)
    m.add(lines, "", ".gitignore")
    return m


class TempTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def write(self, name, text=""):
        full = os.path.join(self.root, *name.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def mkdir(self, name):
        os.makedirs(os.path.join(self.root, *name.split("/")), exist_ok=True)


class ParseTest(unittest.TestCase):
    def test_blank_lines_and_comments_are_skipped(self):
        rules = parse(["", "   ", "# comment", "*.log", "  # not a comment"])
        self.assertEqual([r.pattern for r in rules], ["*.log", "  # not a comment"])
        self.assertEqual([r.lineno for r in rules], [4, 5])

    def test_escaped_hash_and_bang_are_literal(self):
        m = matcher("\\#notes", "\\!important")
        self.assertTrue(m.is_ignored("#notes"))
        self.assertTrue(m.is_ignored("!important"))

    def test_trailing_spaces_dropped_unless_escaped(self):
        self.assertEqual(compile_pattern("a.txt   ").pattern, "a.txt")
        m = matcher("a\\ ")
        self.assertTrue(m.is_ignored("a "))
        self.assertFalse(m.is_ignored("a"))

    def test_line_endings_are_stripped(self):
        self.assertTrue(matcher("a.txt\r\n").is_ignored("a.txt"))

    def test_lone_slash_or_bang_is_nothing(self):
        self.assertIsNone(compile_pattern("/"))
        self.assertIsNone(compile_pattern("!"))

    def test_rule_records_its_flags(self):
        r = compile_pattern("!build/", ".gitignore", 3)
        self.assertTrue(r.negate and r.dir_only)
        self.assertEqual((r.source, r.lineno), (".gitignore", 3))
        self.assertIn("build/", repr(r))


class WildcardTest(unittest.TestCase):
    def test_star_matches_within_one_segment(self):
        m = matcher("*.log")
        self.assertTrue(m.is_ignored("a.log"))
        self.assertTrue(m.is_ignored(".log"))
        self.assertFalse(m.is_ignored("a.log.txt"))

    def test_star_does_not_cross_slashes(self):
        m = matcher("a/*.c")
        self.assertTrue(m.is_ignored("a/x.c"))
        self.assertFalse(m.is_ignored("a/b/x.c"))

    def test_question_mark(self):
        m = matcher("file?.txt")
        self.assertTrue(m.is_ignored("file1.txt"))
        self.assertFalse(m.is_ignored("file10.txt"))
        self.assertFalse(m.is_ignored("file.txt"))

    def test_character_classes(self):
        m = matcher("[abc].txt", "x[0-9].txt", "y[!0-9].txt", "z[^a].txt")
        self.assertTrue(m.is_ignored("b.txt"))
        self.assertFalse(m.is_ignored("d.txt"))
        self.assertTrue(m.is_ignored("x7.txt"))
        self.assertTrue(m.is_ignored("yq.txt"))
        self.assertFalse(m.is_ignored("y5.txt"))
        self.assertTrue(m.is_ignored("zb.txt"))
        self.assertFalse(m.is_ignored("za.txt"))

    def test_negated_class_never_matches_a_slash(self):
        self.assertFalse(matcher("a[!x]b").is_ignored("a/b"))

    def test_bracket_first_in_class_is_literal(self):
        m = matcher("[]a].txt")
        self.assertTrue(m.is_ignored("].txt"))
        self.assertTrue(m.is_ignored("a.txt"))

    def test_unclosed_bracket_is_literal(self):
        m = matcher("a[b")
        self.assertTrue(m.is_ignored("a[b"))
        self.assertFalse(m.is_ignored("ab"))

    def test_escaped_wildcards_are_literal(self):
        m = matcher("\\*.txt", "what\\?")
        self.assertTrue(m.is_ignored("*.txt"))
        self.assertFalse(m.is_ignored("a.txt"))
        self.assertTrue(m.is_ignored("what?"))
        self.assertFalse(m.is_ignored("whats"))

    def test_regex_characters_are_not_special(self):
        m = matcher("a+b(c).txt", "x.y")
        self.assertTrue(m.is_ignored("a+b(c).txt"))
        self.assertFalse(m.is_ignored("aab(c).txt"))
        self.assertFalse(m.is_ignored("xzy"))

    def test_case_sensitive_by_default(self):
        self.assertFalse(matcher("*.LOG").is_ignored("a.log"))
        self.assertTrue(matcher("*.LOG", ignore_case=True).is_ignored("a.log"))


class AnchoringTest(unittest.TestCase):
    def test_no_slash_matches_at_any_depth(self):
        m = matcher("TODO")
        self.assertTrue(m.is_ignored("TODO"))
        self.assertTrue(m.is_ignored("src/deep/TODO"))

    def test_leading_slash_anchors_to_the_root(self):
        m = matcher("/TODO")
        self.assertTrue(m.is_ignored("TODO"))
        self.assertFalse(m.is_ignored("src/TODO"))

    def test_middle_slash_anchors_too(self):
        m = matcher("doc/frotz")
        self.assertTrue(m.is_ignored("doc/frotz"))
        self.assertFalse(m.is_ignored("a/doc/frotz"))

    def test_trailing_slash_alone_does_not_anchor(self):
        m = matcher("build/")
        self.assertTrue(m.is_ignored("build", is_dir=True))
        self.assertTrue(m.is_ignored("x/build", is_dir=True))

    def test_trailing_slash_means_directories_only(self):
        m = matcher("build/")
        self.assertFalse(m.is_ignored("build"))
        self.assertTrue(m.is_ignored("build/out.o"))


class DoubleStarTest(unittest.TestCase):
    def test_leading_double_star_matches_any_depth(self):
        m = matcher("**/foo")
        self.assertTrue(m.is_ignored("foo"))
        self.assertTrue(m.is_ignored("a/b/foo"))

    def test_leading_double_star_with_a_path(self):
        m = matcher("**/foo/bar")
        self.assertTrue(m.is_ignored("foo/bar"))
        self.assertTrue(m.is_ignored("x/foo/bar"))
        self.assertFalse(m.is_ignored("foo/x/bar"))

    def test_trailing_double_star_matches_everything_inside(self):
        m = matcher("abc/**")
        self.assertTrue(m.is_ignored("abc/x"))
        self.assertTrue(m.is_ignored("abc/x/y"))
        self.assertFalse(m.is_ignored("abc", is_dir=True))

    def test_middle_double_star_matches_zero_or_more_directories(self):
        m = matcher("a/**/b")
        self.assertTrue(m.is_ignored("a/b"))
        self.assertTrue(m.is_ignored("a/x/b"))
        self.assertTrue(m.is_ignored("a/x/y/b"))
        self.assertFalse(m.is_ignored("a/xb"))

    def test_double_star_alone(self):
        self.assertTrue(matcher("**").is_ignored("anything/at/all"))

    def test_double_star_inside_a_segment_is_a_plain_star(self):
        m = matcher("a**b")
        self.assertTrue(m.is_ignored("axyzb"))
        self.assertFalse(m.is_ignored("ax/yb"))


class PrecedenceTest(unittest.TestCase):
    def test_last_match_wins(self):
        m = matcher("*.log", "!keep.log")
        self.assertTrue(m.is_ignored("a.log"))
        self.assertFalse(m.is_ignored("keep.log"))
        m = matcher("!keep.log", "*.log")
        self.assertTrue(m.is_ignored("keep.log"))

    def test_cannot_reinclude_inside_an_ignored_directory(self):
        m = matcher("build/", "!build/keep.txt")
        self.assertTrue(m.is_ignored("build/keep.txt"))

    def test_reinclude_works_when_the_directory_itself_is_not_ignored(self):
        m = matcher("build/*", "!build/keep.txt")
        self.assertTrue(m.is_ignored("build/other.txt"))
        self.assertFalse(m.is_ignored("build/keep.txt"))

    def test_negating_a_directory_reincludes_it(self):
        m = matcher("*", "!src/", "!*.py")
        self.assertFalse(m.is_ignored("src", is_dir=True))
        self.assertFalse(m.is_ignored("src/a.py"))
        self.assertTrue(m.is_ignored("src/a.txt"))

    def test_match_returns_the_deciding_rule(self):
        m = matcher("*.log", "!keep.log")
        self.assertEqual(m.match("keep.log").pattern, "!keep.log")
        self.assertIsNone(m.match("a.txt"))

    def test_why_names_the_parent_rule(self):
        m = matcher("build/", "*.o")
        self.assertEqual(m.why("build/x.o").pattern, "build/")
        self.assertIsNone(m.why("src/x.c"))


class NestedFilesTest(unittest.TestCase):
    def test_patterns_are_relative_to_their_directory(self):
        m = IgnoreMatcher()
        m.add(["/gen"], "src", "src/.gitignore")
        self.assertTrue(m.is_ignored("src/gen"))
        self.assertFalse(m.is_ignored("gen"))
        self.assertFalse(m.is_ignored("lib/src/gen"))

    def test_deeper_file_overrides_shallower(self):
        m = IgnoreMatcher()
        m.add(["*.log"], "", ".gitignore")
        m.add(["!*.log"], "logs", "logs/.gitignore")
        self.assertTrue(m.is_ignored("a.log"))
        self.assertFalse(m.is_ignored("logs/a.log"))

    def test_base_prefix_must_be_a_whole_directory(self):
        m = IgnoreMatcher()
        m.add(["*"], "src", "src/.gitignore")
        self.assertFalse(m.is_ignored("srcfoo/a"))


class TreeTest(TempTree):
    def test_walk_lists_what_is_not_ignored(self):
        self.write(".gitignore", "*.log\n!keep.log\nbuild/\n/TODO\n")
        for name in ("a.log", "keep.log", "TODO", "src/TODO", "build/x", "src/main.py"):
            self.write(name)
        self.assertEqual(list(Tree(self.root).walk()), [".gitignore", "keep.log", "src/TODO", "src/main.py"])

    def test_walk_reads_nested_gitignore_files(self):
        self.write(".gitignore", "*.log\n")
        self.write("logs/.gitignore", "!*.log\n")
        self.write("logs/a.log")
        self.write("b.log")
        self.assertEqual(list(Tree(self.root).walk()), [".gitignore", "logs/.gitignore", "logs/a.log"])

    def test_walk_does_not_read_gitignore_inside_ignored_directories(self):
        self.write(".gitignore", "vendor/\n")
        self.write("vendor/.gitignore", "!*\n")
        self.write("vendor/lib.js")
        self.assertEqual(list(Tree(self.root).walk()), [".gitignore"])

    def test_walk_skips_dot_git(self):
        self.write(".git/HEAD", "ref\n")
        self.write("a.txt")
        self.assertEqual(list(Tree(self.root).walk()), ["a.txt"])

    def test_info_exclude_has_the_lowest_precedence(self):
        self.write(".git/info/exclude", "*.tmp\nsecret.txt\n")
        self.write(".gitignore", "!secret.txt\n")
        self.write("a.tmp")
        self.write("secret.txt")
        tree = Tree(self.root)
        self.assertEqual(tree.why("a.tmp").source, ".git/info/exclude")
        self.assertIsNone(tree.why("secret.txt"))

    def test_why_detects_directories_on_disk(self):
        self.write(".gitignore", "out/\n")
        self.mkdir("out")
        self.write("src/out")
        tree = Tree(self.root)
        self.assertIsNotNone(tree.why("out"))
        self.assertIsNone(tree.why("src/out"))

    def test_why_loads_gitignore_files_on_the_way_down(self):
        self.write("a/b/.gitignore", "*.tmp\n")
        self.write("a/b/c/x.tmp")
        rule = Tree(self.root).why("a/b/c/x.tmp")
        self.assertEqual((rule.source, rule.lineno), ("a/b/.gitignore", 1))

    def test_non_utf8_gitignore_does_not_crash(self):
        with open(os.path.join(self.root, ".gitignore"), "wb") as fh:
            fh.write(b"caf\xe9\n*.log\n")
        self.write("a.log")
        self.assertEqual(list(Tree(self.root).walk()), [".gitignore"])


GIT = shutil.which("git")

# Patterns and paths chosen to hit the corners of the rules; each pattern
# set is checked against `git check-ignore` on a real repository.
GIT_CASES = [
    ["*.log", "!keep.log"],
    ["build/", "!build/keep.txt"],
    ["build/*", "!build/keep.txt"],
    ["/TODO", "doc/*.txt", "a/**/b", "**/gen", "abc/**"],
    ["*", "!*/", "!*.py"],
    ["[a-c]*.txt", "x?", "\\#hash", "\\!bang", "sp\\ "],
    ["src/", "!src/"],
    ["*.o", "!lib/*.o", "lib/bad.o"],
]
GIT_PATHS = [
    "a.log", "keep.log", "logs/a.log", "logs/keep.log", "build/keep.txt", "build/x.o", "TODO", "src/TODO",
    "doc/a.txt", "doc/sub/a.txt", "a/b", "a/x/b", "a/x/y/b", "gen/f", "x/gen/f", "abc/z", "abc/q/z", "m.py",
    "src/m.py", "src/m.c", "b1.txt", "d.txt", "xy", "xyz", "#hash", "!bang", "sp ", "lib/a.o", "lib/bad.o",
    "top.o",
]


@unittest.skipIf(GIT is None, "git is not installed")
class AgreesWithGitTest(TempTree):
    def git(self, *args, **kw):
        return subprocess.run([GIT, "-C", self.root, *args], capture_output=True, **kw)

    def test_same_answers_as_git_check_ignore(self):
        for name in GIT_PATHS:
            self.write(name)
        self.git("init", "-q")
        self.git("config", "core.ignorecase", "false")
        for patterns in GIT_CASES:
            with self.subTest(patterns=patterns):
                self.write(".gitignore", "\n".join(patterns) + "\n")
                proc = self.git("check-ignore", "--stdin", "-z", input="\0".join(GIT_PATHS).encode() + b"\0")
                self.assertIn(proc.returncode, (0, 1), proc.stderr)
                by_git = {p for p in proc.stdout.decode().split("\0") if p}
                tree = Tree(self.root)
                ours = {p for p in GIT_PATHS if tree.why(p) is not None}
                self.assertEqual(ours, by_git)


def run_cli(*args):
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True)
    norm = lambda b: b.decode("utf-8").replace("\r\n", "\n")
    return proc.returncode, norm(proc.stdout), norm(proc.stderr)


class CliTest(TempTree):
    def setUp(self):
        super().setUp()
        self.write(".gitignore", "*.log\nbuild/\n")
        self.write("a.log")
        self.write("b.txt")
        self.write("build/out")

    def test_check_prints_ignored_paths_and_exits_0(self):
        self.assertEqual(run_cli("-C", self.root, "a.log", "b.txt", "build/out"), (0, "a.log\nbuild/out\n", ""))

    def test_nothing_ignored_exits_1(self):
        self.assertEqual(run_cli("-C", self.root, "b.txt"), (1, "", ""))

    def test_why(self):
        code, out, _ = run_cli("-C", self.root, "-v", "a.log", "build/out")
        self.assertEqual(code, 0)
        self.assertEqual(out, ".gitignore:1:*.log\ta.log\n.gitignore:2:build/\tbuild/out\n")

    def test_trailing_slash_means_directory(self):
        self.assertEqual(run_cli("-C", self.root, "build/")[0], 0)
        self.assertEqual(run_cli("-C", self.root, "other/")[0], 1)

    def test_paths_that_do_not_exist_are_treated_as_files(self):
        self.assertEqual(run_cli("-C", self.root, "missing.log")[0], 0)
        self.assertEqual(run_cli("-C", self.root, "build")[0], 0)  # exists as a directory

    def test_list(self):
        self.assertEqual(run_cli("-C", self.root, "--list"), (0, ".gitignore\nb.txt\n", ""))

    def test_ignore_case(self):
        self.write("UP.LOG")
        self.assertEqual(run_cli("-C", self.root, "UP.LOG")[0], 1)
        self.assertEqual(run_cli("-C", self.root, "-i", "UP.LOG")[0], 0)

    def test_path_outside_the_tree_is_exit_2(self):
        code, _, err = run_cli("-C", self.root, "../elsewhere.log")
        self.assertEqual(code, 2)
        self.assertIn("outside", err)

    def test_root_itself_is_exit_2(self):
        self.assertEqual(run_cli("-C", self.root, ".")[0], 2)

    def test_missing_directory_is_exit_2(self):
        self.assertEqual(run_cli("-C", os.path.join(self.root, "nope"), "--list")[0], 2)

    def test_usage_errors_are_exit_2(self):
        self.assertEqual(run_cli("-C", self.root)[0], 2)
        self.assertEqual(run_cli("-C", self.root, "--list", "a.log")[0], 2)

    def test_unicode_names(self):
        self.write("日本語.log")
        self.assertEqual(run_cli("-C", self.root, "日本語.log"), (0, "日本語.log\n", ""))


if __name__ == "__main__":
    unittest.main()
