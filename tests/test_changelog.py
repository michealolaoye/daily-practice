import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import changelog  # noqa: E402
from changelog import bump_for, next_version, parse_commit, parse_version, render  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "changelog.py")
HAVE_GIT = shutil.which("git") is not None


def commits(*messages):
    return [parse_commit(m, f"{i:040x}") for i, m in enumerate(messages, 1)]


class ParseCommitTest(unittest.TestCase):
    def test_type_scope_and_description(self):
        c = parse_commit("feat(api): add pagination")
        self.assertEqual((c.type, c.scope, c.description, c.breaking), ("feat", "api", "add pagination", False))
        c = parse_commit("fix: off by one")
        self.assertEqual((c.type, c.scope), ("fix", None))

    def test_type_is_case_insensitive(self):
        self.assertEqual(parse_commit("FEAT: shout").type, "feat")
        self.assertEqual(parse_commit("Fix(Api): x").scope, "Api")

    def test_not_conventional(self):
        for msg in ("Add a thing", "feat:no space", "feat : space before colon", "feat(): empty scope",
                    "feat(a)(b): two scopes", "feat: ", "fe at: x", "", "\n\n", "feat(a:b", "1feat: x"):
            with self.subTest(msg=msg):
                self.assertIsNone(parse_commit(msg))

    def test_bang_marks_breaking(self):
        c = parse_commit("feat(api)!: remove v1")
        self.assertTrue(c.breaking)
        self.assertEqual(c.notes, [])
        self.assertTrue(parse_commit("refactor!: x").breaking)

    def test_breaking_change_footer(self):
        c = parse_commit("feat: x\n\nSome body.\n\nBREAKING CHANGE: config moved to TOML")
        self.assertTrue(c.breaking)
        self.assertEqual(c.notes, ["config moved to TOML"])
        c = parse_commit("fix: x\n\nBREAKING-CHANGE: hyphen form")
        self.assertEqual(c.notes, ["hyphen form"])

    def test_footer_spans_lines_until_the_next_token(self):
        c = parse_commit("feat: x\n\nBREAKING CHANGE: first line\nsecond line\nRefs #12\nReviewed-by: Z")
        self.assertEqual(c.notes, ["first line\nsecond line"])

    def test_several_breaking_footers(self):
        c = parse_commit("feat: x\n\nBREAKING CHANGE: one\nBREAKING CHANGE: two")
        self.assertEqual(c.notes, ["one", "two"])

    def test_breaking_change_only_counts_in_the_footer_paragraph(self):
        c = parse_commit("fix: x\n\nBREAKING CHANGE: mentioned in the body\n\nRefs #1")
        self.assertFalse(c.breaking)
        # and only in upper case
        self.assertFalse(parse_commit("fix: x\n\nbreaking change: lower case").breaking)
        # a header with no body can't have footers
        self.assertFalse(parse_commit("fix: BREAKING CHANGE: in the header").breaking)

    def test_crlf_messages(self):
        c = parse_commit("feat(x): y\r\n\r\nBREAKING CHANGE: z\r\n")
        self.assertEqual((c.scope, c.description, c.notes), ("x", "y", ["z"]))


class BumpTest(unittest.TestCase):
    def test_highest_bump_wins(self):
        self.assertEqual(bump_for(commits("fix: a")), "patch")
        self.assertEqual(bump_for(commits("perf: a")), "patch")
        self.assertEqual(bump_for(commits("fix: a", "feat: b", "fix: c")), "minor")
        self.assertEqual(bump_for(commits("feat: a", "docs!: b", "fix: c")), "major")
        self.assertEqual(bump_for(commits("fix: a", "chore: x\n\nBREAKING CHANGE: y")), "major")

    def test_nothing_releasable(self):
        self.assertIsNone(bump_for([]))
        self.assertIsNone(bump_for(commits("docs: a", "chore: b", "refactor: c", "test: d", "ci: e")))

    def test_breaking_before_1_0_is_minor(self):
        self.assertEqual(bump_for(commits("feat!: a"), "0.4.2"), "minor")
        self.assertEqual(bump_for(commits("feat!: a"), "v1.0.0"), "major")
        self.assertEqual(bump_for(commits("fix: a"), "0.4.2"), "patch")


class VersionTest(unittest.TestCase):
    def test_next_version_keeps_the_prefix(self):
        self.assertEqual(next_version("1.2.3", "patch"), "1.2.4")
        self.assertEqual(next_version("v1.2.3", "minor"), "v1.3.0")
        self.assertEqual(next_version("v1.2.3", "major"), "v2.0.0")
        self.assertEqual(next_version("0.0.0", "patch"), "0.0.1")
        self.assertEqual(next_version("1.9.9", "patch"), "1.9.10")

    def test_bad_versions(self):
        for bad in ("1.2", "1.2.3.4", "v1.2.3-rc.1", "01.2.3", "release-1", "V1.2.3", ""):
            with self.subTest(version=bad), self.assertRaises(ValueError):
                parse_version(bad)
        with self.assertRaises(ValueError):
            next_version("1.2.3", "huge")


class RenderTest(unittest.TestCase):
    def test_sections_in_order_with_scopes_and_shas(self):
        cs = commits("fix(api): b", "feat: a", "docs: ignored", "perf: c", "feat(cli)!: d\n\nBREAKING CHANGE: e\nf")
        self.assertEqual(render("v2.0.0", "2026-11-12", cs), "\n".join([
            "## v2.0.0 (2026-11-12)",
            "",
            "### Breaking changes",
            "",
            "- **cli:** d (0000000)",
            "  e",
            "  f",
            "",
            "### Features",
            "",
            "- a (0000000)",
            "",
            "### Bug fixes",
            "",
            "- **api:** b (0000000)",
            "",
            "### Performance",
            "",
            "- c (0000000)",
            "",
        ]))

    def test_other_changes_only_with_include_other(self):
        cs = commits("fix: a", "docs(readme): b", "chore: c")
        self.assertNotIn("Other", render("1.0.1", "d", cs))
        text = render("1.0.1", "d", cs, include_other=True)
        self.assertIn("### Other changes\n\n- docs: **readme:** b (0000000)\n- chore: c (0000000)\n", text)

    def test_no_sha(self):
        self.assertIn("- x\n", render("1.0.0", "d", [parse_commit("feat: x")]))

    def test_note_indent_is_normalised(self):
        cs = commits("feat!: a\n\nBREAKING CHANGE: one\n      two")
        self.assertIn("- a (0000000)\n  one\n  two\n", render("1.0.0", "d", cs))


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class RepoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = changelog.main(["-C", self.repo, "--date", "2026-11-12", *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_since_the_last_tag(self):
        self.commit("feat: before the tag")
        self.git("tag", "v1.4.0")
        sha = self.commit("fix(io): close the file")
        self.commit("docs: typo")
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, f"## v1.4.1 (2026-11-12)\n\n### Bug fixes\n\n- **io:** close the file ({sha[:7]})\n")
        self.assertEqual(err, "")

    def test_next_only(self):
        self.commit("feat: a")
        self.git("tag", "2.0.0")
        self.commit("feat: b")
        self.assertEqual(self.run_main("--next")[:2], (0, "2.1.0\n"))

    def test_no_tags_means_all_history_from_0_0_0(self):
        self.commit("feat: a")
        self.commit("fix: b")
        self.assertEqual(self.run_main("--next")[:2], (0, "0.1.0\n"))

    def test_oldest_first(self):
        self.commit("feat: one")
        self.commit("feat: two")
        out = self.run_main()[1]
        self.assertLess(out.index("one"), out.index("two"))

    def test_nothing_to_release_exits_1(self):
        self.commit("feat: a")
        self.git("tag", "v1.0.0")
        self.commit("docs: only docs")
        code, out, err = self.run_main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("nothing to release since v1.0.0", err)

    def test_nothing_at_all_since_the_tag(self):
        self.commit("feat: a")
        self.git("tag", "v1.0.0")
        self.assertEqual(self.run_main()[0], 1)

    def test_non_conventional_commits_warn_and_strict_fails(self):
        self.commit("feat: a")
        sha = self.commit("Fixed stuff")
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn(f"not a Conventional Commit: {sha[:7]} Fixed stuff", err)
        code, out, _ = self.run_main("--strict")
        self.assertEqual((code, out), (1, ""))

    def test_merge_commits_are_skipped(self):
        self.commit("feat: base")
        self.git("tag", "v1.0.0")
        self.git("checkout", "-q", "-b", "side")
        self.commit("fix: on the side")
        self.git("checkout", "-q", "-")
        self.commit("feat: on main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("on the side", out)
        self.assertIn("on main", out)
        self.assertNotIn("Merge", err)

    def test_since_overrides_the_tag(self):
        first = self.commit("feat: a")
        self.commit("fix: b")
        code, out, _ = self.run_main("--since", first, "--current", "3.0.0")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("## 3.0.1 "))
        self.assertNotIn("- a ", out)

    def test_tag_that_is_not_a_version_needs_current(self):
        self.commit("feat: a")
        self.git("tag", "release-candidate")
        self.commit("fix: b")
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("use --current", err)
        self.assertEqual(self.run_main("--current", "v0.9.0", "--next")[:2], (0, "v0.9.1\n"))

    def test_breaking_before_1_0(self):
        self.commit("feat: a")
        self.git("tag", "v0.3.1")
        self.commit("feat!: b")
        self.assertEqual(self.run_main("--next")[1], "v0.4.0\n")

    def test_unicode_and_multi_paragraph_messages(self):
        self.commit("feat(i18n): résumé support ✓\n\nLong body\n\nwith paragraphs.")
        self.assertIn("- **i18n:** résumé support ✓ (", self.run_main()[1])

    def test_option_like_since_is_refused(self):
        self.commit("feat: a")
        code, _, err = self.run_main("--since=--output=x")
        self.assertEqual(code, 2)
        self.assertIn("not a revision", err)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "x")))

    def test_errors_exit_2(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        code, out, err = self.run_main("-C", empty.name)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("changelog:", err)
        self.commit("feat: a")
        self.assertEqual(self.run_main("--since", "no-such-ref")[0], 2)
        self.assertEqual(self.run_main("--current", "1.2")[0], 2)

    def test_bad_date_is_a_usage_error(self):
        self.commit("feat: a")
        code, _, err = self.run_main("--date", "12/11/2026")
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)

    def test_cli_end_to_end(self):
        self.commit("feat: a")
        proc = subprocess.run([sys.executable, SCRIPT, "-C", self.repo, "--next"], capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout.replace("\r\n", "\n")), (0, "0.1.0\n"))


if __name__ == "__main__":
    unittest.main()
