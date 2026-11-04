import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import mdtable  # noqa: E402
from mdtable import display_width, format_text, parse_alignments, split_row  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "mdtable.py")


class DisplayWidthTest(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(display_width("hello"), 5)
        self.assertEqual(display_width(""), 0)

    def test_wide_characters_count_two(self):
        self.assertEqual(display_width("日本語"), 6)
        self.assertEqual(display_width("ＡＢ"), 4)  # fullwidth Latin

    def test_combining_accent_has_no_width(self):
        self.assertEqual(display_width("e\u0301"), 1)

    def test_zero_width_space_has_no_width(self):
        self.assertEqual(display_width("a\u200bb"), 2)

    def test_precomposed_accent(self):
        self.assertEqual(display_width("\u00e9"), 1)


class SplitRowTest(unittest.TestCase):
    def test_with_outer_pipes(self):
        self.assertEqual(split_row("| a | b |"), ["a", "b"])

    def test_without_outer_pipes(self):
        self.assertEqual(split_row("a | b"), ["a", "b"])

    def test_only_a_leading_pipe(self):
        self.assertEqual(split_row("| a | b"), ["a", "b"])

    def test_only_a_trailing_pipe(self):
        self.assertEqual(split_row("a | b |"), ["a", "b"])

    def test_empty_cells_are_kept(self):
        self.assertEqual(split_row("| a | | c |"), ["a", "", "c"])
        self.assertEqual(split_row("||"), [""])

    def test_escaped_pipe_stays_in_the_cell(self):
        self.assertEqual(split_row(r"| a \| b | c |"), [r"a \| b", "c"])

    def test_escaped_pipe_at_the_end_is_content_not_a_border(self):
        self.assertEqual(split_row(r"a | b \|"), ["a", r"b \|"])

    def test_other_backslashes_are_kept(self):
        self.assertEqual(split_row(r"| C:\dir | x |"), [r"C:\dir", "x"])

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(split_row("   |  a   |   b  |   "), ["a", "b"])

    def test_single_cell(self):
        self.assertEqual(split_row("| a |"), ["a"])


class AlignmentTest(unittest.TestCase):
    def test_all_kinds(self):
        self.assertEqual(parse_alignments(["---", ":--", "--:", ":-:"]), [None, "left", "right", "center"])

    def test_single_dash_is_enough(self):
        self.assertEqual(parse_alignments(["-", ":-", "-:"]), [None, "left", "right"])

    def test_not_a_delimiter_row(self):
        for cells in (["a", "b"], ["---", "x"], ["::"], [""], []):
            self.assertIsNone(parse_alignments(cells), cells)


class FormatTableTest(unittest.TestCase):
    def test_columns_are_padded_to_the_widest_cell(self):
        text = "| a | bb |\n|---|---|\n| cccc | d |\n"
        self.assertEqual(format_text(text),
                         "| a    | bb  |\n| ---- | --- |\n| cccc | d   |\n")

    def test_minimum_width_is_three(self):
        self.assertEqual(format_text("|a|\n|-|\n|b|\n"), "| a   |\n| --- |\n| b   |\n")

    def test_alignments(self):
        text = "| l | c | r | n |\n|:--|:-:|--:|---|\n| 1 | 2 | 3 | 4 |\n"
        got = format_text(text).split("\n")
        self.assertEqual(got[1], "| :-- | :-: | --: | --- |")
        self.assertEqual(got[2], "| 1   |  2  |   3 | 4   |")

    def test_right_aligned_text_is_padded_on_the_left(self):
        got = format_text("| n |\n|--:|\n| 5 |\n| 100 |\n").split("\n")
        self.assertEqual(got[2], "|   5 |")
        self.assertEqual(got[3], "| 100 |")

    def test_center_puts_the_extra_space_on_the_right(self):
        got = format_text("| h |\n|:-:|\n| ab |\n").split("\n")
        self.assertEqual(got[0], "|  h  |")
        self.assertEqual(got[2], "| ab  |")

    def test_delimiter_width_matches_the_column_for_every_alignment(self):
        text = "| aaaaaaaa | bbbbbbbb | cccccccc | dddddddd |\n| --- | :-- | --: | :-: |\n"
        delimiter = format_text(text).split("\n")[1]
        self.assertEqual(delimiter, "| -------- | :------- | -------: | :------: |")

    def test_short_rows_are_padded_with_empty_cells(self):
        got = format_text("| a | b | c |\n|---|---|---|\n| 1 |\n").split("\n")
        self.assertEqual(got[2], "| 1   |     |     |")

    def test_row_with_too_many_cells_is_an_error_naming_the_line(self):
        with self.assertRaises(ValueError) as ctx:
            format_text("intro\n\n| a | b |\n|---|---|\n| 1 | 2 | 3 |\n")
        self.assertIn("line 5", str(ctx.exception))

    def test_wide_characters_line_up(self):
        got = format_text("| a | b |\n|---|---|\n| 日本語 | x |\n").split("\n")
        self.assertEqual(got[0], "| a      | b   |")
        self.assertEqual(got[2], "| 日本語 | x   |")
        self.assertEqual(display_width(got[0].split("|")[1]), display_width(got[2].split("|")[1]))

    def test_escaped_pipe_is_kept_and_counted(self):
        got = format_text("| a | b |\n|---|---|\n| x \\| y | z |\n").split("\n")
        self.assertEqual(got[2], "| x \\| y | z   |")

    def test_no_outer_pipes_gain_them(self):
        got = format_text("a | b\n--|--\n1 | 2\n")
        self.assertEqual(got, "| a   | b   |\n| --- | --- |\n| 1   | 2   |\n")

    def test_header_only_table(self):
        self.assertEqual(format_text("| a | b |\n|-|-|\n"), "| a   | b   |\n| --- | --- |\n")

    def test_empty_cells_in_the_header(self):
        got = format_text("| | b |\n|-|-|\n| 1 | 2 |\n").split("\n")
        self.assertEqual(got[0], "|     | b   |")


class SurroundingTextTest(unittest.TestCase):
    def test_text_without_tables_is_unchanged(self):
        text = "# Title\n\nSome | pipes | here but no delimiter row.\n\n- list\n"
        self.assertEqual(format_text(text), text)

    def test_empty_text(self):
        self.assertEqual(format_text(""), "")

    def test_text_around_a_table_is_untouched(self):
        text = "before  \n\n| a | b |\n|-|-|\n| 1 | 2 |\n\nafter  \n"
        got = format_text(text)
        self.assertTrue(got.startswith("before  \n\n"))
        self.assertTrue(got.endswith("\n\nafter  \n"))

    def test_two_tables_in_one_file(self):
        text = "| a |\n|-|\n| 1 |\n\ntext\n\n| bb |\n|-|\n| 22 |\n"
        got = format_text(text)
        self.assertIn("| a   |", got)
        self.assertIn("| bb  |", got)

    def test_table_ends_at_a_line_without_a_pipe(self):
        got = format_text("| a |\n|-|\n| 1 |\nnot a row\n").split("\n")
        self.assertEqual(got[3], "not a row")

    def test_table_ends_at_a_blank_line(self):
        got = format_text("| a |\n|-|\n| 1 |\n\n| z | y |\n").split("\n")
        self.assertEqual(got[4], "| z | y |")

    def test_delimiter_with_the_wrong_number_of_cells_is_not_a_table(self):
        text = "| a | b |\n|---|\n| 1 | 2 |\n"
        self.assertEqual(format_text(text), text)

    def test_tables_in_fenced_code_blocks_are_left_alone(self):
        text = "```\n| a | b |\n|-|-|\n| 1 | 2 |\n```\n"
        self.assertEqual(format_text(text), text)

    def test_tilde_fences_and_longer_fences(self):
        for text in ("~~~\n|a|\n|-|\n~~~\n", "````\n|a|\n|-|\n````\n"):
            self.assertEqual(format_text(text), text)

    def test_a_table_after_a_closed_fence_is_formatted(self):
        got = format_text("```\ncode\n```\n|a|\n|-|\n")
        self.assertTrue(got.endswith("| a   |\n| --- |\n"))

    def test_a_different_fence_marker_does_not_close_a_fence(self):
        text = "```\n~~~\n|a|\n|-|\n```\n"
        self.assertEqual(format_text(text), text)

    def test_quoted_tables_are_left_alone(self):
        text = "> | a | b |\n> |-|-|\n"
        self.assertEqual(format_text(text), text)

    def test_indented_table_keeps_its_indent(self):
        got = format_text("  |a|\n  |-|\n  |b|\n")
        self.assertEqual(got, "  | a   |\n  | --- |\n  | b   |\n")

    def test_four_space_indent_is_a_code_block_not_a_table(self):
        text = "    |a|\n    |-|\n"
        self.assertEqual(format_text(text), text)


class LineEndingTest(unittest.TestCase):
    def test_crlf_is_preserved(self):
        got = format_text("|a|\r\n|-|\r\n|b|\r\n")
        self.assertEqual(got, "| a   |\r\n| --- |\r\n| b   |\r\n")
        self.assertNotIn("\n\n", got)
        self.assertEqual(got.count("\r\n"), got.count("\n"))

    def test_missing_final_newline_stays_missing(self):
        self.assertFalse(format_text("|a|\n|-|\n|b|").endswith("\n"))

    def test_final_newline_stays(self):
        self.assertTrue(format_text("|a|\n|-|\n|b|\n").endswith("\n"))

    def test_idempotent(self):
        text = "x\n\n|Name|Qty|\n|:-|-:|\n|日本|1|\n|b \\| c|22|\n\ny\n"
        once = format_text(text)
        self.assertEqual(format_text(once), once)


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, SCRIPT, *args], input=stdin,
                          capture_output=True, timeout=30)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


UNTIDY = "|a|b|\n|-|-|\n|1|22|\n"
TIDY = "| a   | b   |\n| --- | --- |\n| 1   | 22  |\n"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name, text=None, mode="w"):
        full = os.path.join(self.tmp.name, name)
        if text is not None:
            with open(full, mode, encoding=None if "b" in mode else "utf-8", newline=None if "b" in mode else "") as h:
                h.write(text)
        return full

    def read(self, full):
        with open(full, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_stdin_to_stdout(self):
        self.assertEqual(run_cli(stdin=UNTIDY.encode())[:2], (0, TIDY))

    def test_file_to_stdout_leaves_the_file_alone(self):
        p = self.path("a.md", UNTIDY)
        self.assertEqual(run_cli(p)[:2], (0, TIDY))
        self.assertEqual(self.read(p), UNTIDY)

    def test_in_place(self):
        p = self.path("a.md", UNTIDY)
        code, out, _ = run_cli("-i", p)
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(self.read(p), TIDY)

    def test_in_place_several_files(self):
        a, b = self.path("a.md", UNTIDY), self.path("b.md", UNTIDY)
        self.assertEqual(run_cli("-i", a, b)[0], 0)
        self.assertEqual((self.read(a), self.read(b)), (TIDY, TIDY))

    def test_in_place_keeps_crlf(self):
        p = self.path("a.md", UNTIDY.replace("\n", "\r\n"))
        run_cli("-i", p)
        self.assertEqual(self.read(p), TIDY.replace("\n", "\r\n"))

    def test_in_place_does_not_touch_a_file_that_is_already_tidy(self):
        p = self.path("a.md", TIDY)
        before = os.stat(p).st_mtime_ns
        run_cli("-i", p)
        self.assertEqual(os.stat(p).st_mtime_ns, before)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_in_place_keeps_permissions(self):
        p = self.path("a.md", UNTIDY)
        os.chmod(p, 0o640)
        run_cli("-i", p)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o640)

    def test_in_place_leaves_no_temporary_files(self):
        p = self.path("a.md", UNTIDY)
        run_cli("-i", p)
        self.assertEqual(os.listdir(self.tmp.name), ["a.md"])

    def test_check_lists_files_that_would_change(self):
        a, b = self.path("a.md", UNTIDY), self.path("b.md", TIDY)
        code, out, _ = run_cli("--check", a, b)
        self.assertEqual(code, 1)
        self.assertEqual(out.split(), [a])
        self.assertEqual(self.read(a), UNTIDY)

    def test_check_passes_on_tidy_files(self):
        self.assertEqual(run_cli("--check", self.path("b.md", TIDY))[:2], (0, ""))

    def test_check_and_in_place_need_files(self):
        self.assertEqual(run_cli("--check", stdin=b"")[0], 2)
        self.assertEqual(run_cli("-i", stdin=b"")[0], 2)

    def test_check_and_in_place_cannot_be_combined(self):
        self.assertEqual(run_cli("--check", "-i", self.path("a.md", TIDY))[0], 2)

    def test_missing_file_exits_2(self):
        code, _, err = run_cli(os.path.join(self.tmp.name, "nope.md"))
        self.assertEqual(code, 2)
        self.assertIn("mdtable:", err)

    def test_bad_table_exits_3_and_names_the_file(self):
        p = self.path("bad.md", "|a|\n|-|\n|1|2|\n")
        code, out, err = run_cli(p)
        self.assertEqual((code, out), (3, ""))
        self.assertIn("bad.md", err)
        self.assertIn("line 3", err)

    def test_bad_file_does_not_stop_the_good_ones(self):
        bad, good = self.path("bad.md", "|a|\n|-|\n|1|2|\n"), self.path("good.md", UNTIDY)
        self.assertEqual(run_cli("-i", bad, good)[0], 3)
        self.assertEqual(self.read(good), TIDY)

    def test_invalid_utf8_exits_3_without_a_traceback(self):
        p = self.path("bin.md", b"|a|\n\xff\n", mode="wb")
        code, _, err = run_cli(p)
        self.assertEqual(code, 3)
        self.assertNotIn("Traceback", err)

    def test_unicode_survives_a_round_trip(self):
        text = "| 名前 | n |\n| - | - |\n| café | 1 |\n"
        code, out, _ = run_cli(stdin=text.encode("utf-8"))
        self.assertEqual(code, 0)
        self.assertIn("café", out)
        self.assertIn("名前", out)

    def test_stdout_line_endings_are_not_translated(self):
        proc = subprocess.run([sys.executable, SCRIPT], input=UNTIDY.encode(), capture_output=True, timeout=30)
        self.assertNotIn(b"\r", proc.stdout)


if __name__ == "__main__":
    unittest.main()
