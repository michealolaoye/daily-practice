import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import jsonl  # noqa: E402
from jsonl import MISSING, get_path, matches, parse_condition  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "jsonl.py")


class GetPathTest(unittest.TestCase):
    def test_nested_object(self):
        self.assertEqual(get_path({"a": {"b": {"c": 1}}}, "a.b.c"), 1)

    def test_list_index(self):
        self.assertEqual(get_path({"items": [{"id": 7}, {"id": 8}]}, "items.1.id"), 8)

    def test_missing_key(self):
        self.assertIs(get_path({"a": 1}, "b"), MISSING)

    def test_missing_deeper(self):
        self.assertIs(get_path({"a": {}}, "a.b.c"), MISSING)

    def test_index_out_of_range(self):
        self.assertIs(get_path({"a": [1]}, "a.5"), MISSING)

    def test_null_is_a_value_not_missing(self):
        self.assertIsNone(get_path({"a": None}, "a"))

    def test_false_and_zero_and_empty_are_values(self):
        self.assertIs(get_path({"a": False}, "a"), False)
        self.assertEqual(get_path({"a": 0}, "a"), 0)
        self.assertEqual(get_path({"a": ""}, "a"), "")

    def test_stepping_into_a_scalar_is_missing(self):
        self.assertIs(get_path({"a": 5}, "a.b"), MISSING)
        self.assertIs(get_path({"a": "text"}, "a.0"), MISSING)

    def test_non_numeric_list_step_is_missing(self):
        self.assertIs(get_path({"a": [1]}, "a.x"), MISSING)

    def test_top_level_list(self):
        self.assertEqual(get_path([10, 20], "1"), 20)


class ConditionTest(unittest.TestCase):
    def test_operators(self):
        for text, op in [("a=1", "="), ("a!=1", "!="), ("a>1", ">"), ("a>=1", ">="),
                         ("a<1", "<"), ("a<=1", "<=")]:
            self.assertEqual(parse_condition(text), ("a", op, 1), text)

    def test_value_types(self):
        self.assertEqual(parse_condition("a=true")[2], True)
        self.assertEqual(parse_condition("a=null")[2], None)
        self.assertEqual(parse_condition("a=1.5")[2], 1.5)
        self.assertEqual(parse_condition('a="x y"')[2], "x y")

    def test_plain_text_value_needs_no_quotes(self):
        self.assertEqual(parse_condition("name=bob"), ("name", "=", "bob"))

    def test_value_may_contain_operator_characters(self):
        self.assertEqual(parse_condition("q=a=b"), ("q", "=", "a=b"))

    def test_dotted_path(self):
        self.assertEqual(parse_condition("user.id=3"), ("user.id", "=", 3))

    def test_empty_value(self):
        self.assertEqual(parse_condition("a="), ("a", "=", ""))

    def test_not_a_condition(self):
        for bad in ["", "abc", "=1", "  =1", ">5"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_condition(bad)


class MatchesTest(unittest.TestCase):
    def check(self, record, text):
        return matches(record, parse_condition(text))

    def test_equality(self):
        self.assertTrue(self.check({"s": 200}, "s=200"))
        self.assertFalse(self.check({"s": 201}, "s=200"))

    def test_string_equality_without_quotes(self):
        self.assertTrue(self.check({"n": "bob"}, "n=bob"))

    def test_number_does_not_equal_its_text(self):
        self.assertFalse(self.check({"s": "200"}, "s=200"))

    def test_true_is_not_one(self):
        self.assertFalse(self.check({"a": True}, "a=1"))
        self.assertFalse(self.check({"a": 1}, "a=true"))
        self.assertTrue(self.check({"a": True}, "a=true"))
        self.assertFalse(self.check({"a": False}, "a=0"))

    def test_null_matches_null_only(self):
        self.assertTrue(self.check({"a": None}, "a=null"))
        self.assertFalse(self.check({}, "a=null"))

    def test_missing_field_is_not_equal_to_anything(self):
        self.assertFalse(self.check({}, "a=1"))
        self.assertTrue(self.check({}, "a!=1"))

    def test_not_equal(self):
        self.assertTrue(self.check({"a": 2}, "a!=1"))
        self.assertFalse(self.check({"a": 1}, "a!=1"))

    def test_ordering_numbers(self):
        self.assertTrue(self.check({"s": 500}, "s>=500"))
        self.assertFalse(self.check({"s": 499}, "s>=500"))
        self.assertTrue(self.check({"s": 499}, "s<500"))
        self.assertTrue(self.check({"s": 500}, "s<=500"))
        self.assertFalse(self.check({"s": 500}, "s>500"))

    def test_int_and_float_compare(self):
        self.assertTrue(self.check({"s": 2}, "s<2.5"))

    def test_ordering_strings(self):
        self.assertTrue(self.check({"d": "2026-11-03"}, "d>2026-11-01"))
        self.assertFalse(self.check({"d": "2026-10-30"}, "d>2026-11-01"))

    def test_ordering_across_types_is_false_not_an_error(self):
        self.assertFalse(self.check({"a": "10"}, "a>5"))
        self.assertFalse(self.check({"a": 10}, "a>x"))
        self.assertFalse(self.check({"a": None}, "a>5"))
        self.assertFalse(self.check({"a": True}, "a>0"))
        self.assertFalse(self.check({"a": [1]}, "a>0"))

    def test_ordering_on_missing_field_is_false(self):
        for op in ["<", "<=", ">", ">="]:
            self.assertFalse(self.check({}, f"a{op}1"))

    def test_nested_path(self):
        self.assertTrue(self.check({"user": {"name": "ada"}}, "user.name=ada"))


class SelectTest(unittest.TestCase):
    def test_keeps_only_requested_paths(self):
        rec = {"a": 1, "b": {"c": 2}, "d": 3}
        self.assertEqual(jsonl.select(rec, ["a", "b.c"]), {"a": 1, "b.c": 2})

    def test_missing_becomes_null(self):
        self.assertEqual(jsonl.select({"a": 1}, ["a", "z"]), {"a": 1, "z": None})

    def test_key_order_follows_the_request(self):
        self.assertEqual(list(jsonl.select({"a": 1, "b": 2}, ["b", "a"])), ["b", "a"])


class RecordsTest(unittest.TestCase):
    def test_numbers_lines_and_skips_blanks(self):
        got = list(jsonl.records(['{"a":1}\n', "\n", "  \n", '{"a":2}\n']))
        self.assertEqual(got, [(1, {"a": 1}), (4, {"a": 2})])

    def test_non_object_values_are_fine(self):
        self.assertEqual([v for _, v in jsonl.records(["1\n", '"x"\n', "[1]\n", "null\n"])],
                         [1, "x", [1], None])

    def test_bad_line_raises_with_its_number(self):
        with self.assertRaises(ValueError) as ctx:
            list(jsonl.records(['{"a":1}\n', "oops\n"]))
        self.assertIn("line 2", str(ctx.exception))

    def test_skip_bad_reports_and_continues(self):
        bad = []
        got = list(jsonl.records(["x\n", '{"a":1}\n', "{\n"], skip_bad=True, on_bad=bad.append))
        self.assertEqual(got, [(2, {"a": 1})])
        self.assertEqual(bad, [1, 3])

    def test_truncated_final_line(self):
        with self.assertRaises(ValueError):
            list(jsonl.records(['{"a":1}\n', '{"a":']))

    def test_crlf(self):
        self.assertEqual(list(jsonl.records(['{"a":1}\r\n'])), [(1, {"a": 1})])

    def test_is_lazy(self):
        seen = []

        def lines():
            for text in ['{"a":1}', "bad", '{"a":3}']:
                seen.append(text)
                yield text

        gen = jsonl.records(lines())
        next(gen)
        self.assertEqual(seen, ['{"a":1}'])


class CountByTest(unittest.TestCase):
    def test_most_common_first(self):
        items = [{"p": "/a"}, {"p": "/b"}, {"p": "/b"}, {"p": "/c"}, {"p": "/b"}, {"p": "/a"}]
        self.assertEqual(jsonl.count_by(items, "p"), [("/b", 3), ("/a", 2), ("/c", 1)])

    def test_ties_keep_first_seen_order(self):
        self.assertEqual(jsonl.count_by([{"p": "z"}, {"p": "a"}], "p"), [("z", 1), ("a", 1)])

    def test_missing_counts_as_null(self):
        self.assertEqual(jsonl.count_by([{"p": 1}, {}, {}], "p"), [(None, 2), (1, 1)])

    def test_number_and_text_are_different_values(self):
        got = jsonl.count_by([{"p": 1}, {"p": "1"}], "p")
        self.assertEqual([(type(v).__name__, n) for v, n in got], [("int", 1), ("str", 1)])

    def test_true_and_one_are_different_values(self):
        self.assertEqual(len(jsonl.count_by([{"p": True}, {"p": 1}], "p")), 2)

    def test_structured_values_are_counted_by_content(self):
        got = jsonl.count_by([{"p": [1, 2]}, {"p": [1, 2]}, {"p": [2, 1]}], "p")
        self.assertEqual(got, [([1, 2], 2), ([2, 1], 1)])

    def test_empty(self):
        self.assertEqual(jsonl.count_by([], "p"), [])


def run_cli(*args, stdin=""):
    proc = subprocess.run([sys.executable, SCRIPT, *args], input=stdin,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


LOG = "\n".join([
    '{"ts":1,"status":200,"path":"/a","user":{"name":"ada"}}',
    '{"ts":2,"status":500,"path":"/b","user":{"name":"bob"}}',
    '{"ts":3,"status":503,"path":"/b"}',
    '{"ts":4,"status":200,"path":"/b","user":{"name":"ada"}}',
]) + "\n"


def lines_of(text):
    return [json.loads(line) for line in text.splitlines()]


class CliTest(unittest.TestCase):
    def test_passes_records_through_compactly(self):
        code, out, _ = run_cli(stdin='{ "a" : 1 }\n[1, 2]\n')
        self.assertEqual((code, out), (0, '{"a":1}\n[1,2]\n'))

    def test_where(self):
        code, out, _ = run_cli("-w", "status>=500", stdin=LOG)
        self.assertEqual(code, 0)
        self.assertEqual([r["ts"] for r in lines_of(out)], [2, 3])

    def test_several_where_clauses_must_all_match(self):
        code, out, _ = run_cli("-w", "status=200", "-w", "path=/b", stdin=LOG)
        self.assertEqual([r["ts"] for r in lines_of(out)], [4])

    def test_nested_where(self):
        _, out, _ = run_cli("-w", "user.name=ada", stdin=LOG)
        self.assertEqual([r["ts"] for r in lines_of(out)], [1, 4])

    def test_select(self):
        _, out, _ = run_cli("-s", "ts,user.name", "-w", "status=503", stdin=LOG)
        self.assertEqual(lines_of(out), [{"ts": 3, "user.name": None}])

    def test_limit(self):
        _, out, _ = run_cli("-n", "2", stdin=LOG)
        self.assertEqual(len(lines_of(out)), 2)

    def test_limit_zero(self):
        self.assertEqual(run_cli("-n", "0", stdin=LOG)[:2], (0, ""))

    def test_limit_counts_matches_not_input_lines(self):
        _, out, _ = run_cli("-n", "1", "-w", "status=500", stdin=LOG)
        self.assertEqual([r["ts"] for r in lines_of(out)], [2])

    def test_count_by(self):
        _, out, _ = run_cli("-c", "path", stdin=LOG)
        self.assertEqual(lines_of(out), [{"value": "/b", "count": 3}, {"value": "/a", "count": 1}])

    def test_count_by_after_filter(self):
        _, out, _ = run_cli("-c", "user.name", "-w", "status=200", stdin=LOG)
        self.assertEqual(lines_of(out), [{"value": "ada", "count": 2}])

    def test_unicode_is_not_escaped(self):
        _, out, _ = run_cli(stdin='{"n":"caf\\u00e9"}\n')
        self.assertEqual(out, '{"n":"café"}\n')

    def test_text_outside_the_windows_code_page_round_trips(self):
        # Not representable in cp1252, so it fails if the streams are left
        # in the locale encoding.
        text = '{"n":"日本語 \U0001f600"}\n'
        code, out, _ = run_cli("-w", "n=日本語 \U0001f600", stdin=text)
        self.assertEqual((code, out), (0, text))

    def test_bad_line_exits_1_after_earlier_output(self):
        code, out, err = run_cli(stdin='{"a":1}\nnope\n{"a":3}\n')
        self.assertEqual(code, 1)
        self.assertEqual(out, '{"a":1}\n')
        self.assertIn("line 2", err)

    def test_skip_bad_continues_and_says_how_many(self):
        code, out, err = run_cli("--skip-bad", stdin='{"a":1}\nnope\n{"a":3}\n\x7b\n')
        self.assertEqual(code, 0)
        self.assertEqual(out, '{"a":1}\n{"a":3}\n')
        self.assertIn("skipped 2 line(s)", err)

    def test_skip_bad_report_is_shortened(self):
        _, _, err = run_cli("--skip-bad", stdin="x\n" * 8)
        self.assertIn("skipped 8", err)
        self.assertIn("...", err)

    def test_reads_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(LOG)
            code, out, _ = run_cli("-w", "ts=1", path)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines_of(out)), 1)

    def test_missing_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_cli(os.path.join(tmp, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("jsonl:", err)

    def test_directory_as_input_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_cli(tmp)[0], 2)

    def test_usage_errors_exit_2(self):
        for args in (["-w", "nonsense"], ["-n", "-1"], ["-n", "x"], ["-s", "a,,b"],
                     ["-c", "a", "-s", "b"], ["-c", "a", "-n", "1"], ["--bogus"]):
            self.assertEqual(run_cli(*args, stdin=LOG)[0], 2, args)

    def test_invalid_utf8_exits_1_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.jsonl")
            with open(path, "wb") as handle:
                handle.write(b'{"a":"\xff"}\n')
            code, _, err = run_cli(path)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)

    def test_empty_input(self):
        self.assertEqual(run_cli(stdin="")[:2], (0, ""))

    def test_closed_pipe_is_not_an_error(self):
        # Far more output than a pipe buffers: read one line, then hang up.
        # The input comes from a file so that feeding it cannot deadlock
        # against the child's blocked output.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"i":1}\n' * 200000)
            proc = subprocess.Popen([sys.executable, SCRIPT, path],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                first = proc.stdout.readline()
                proc.stdout.close()
                err = proc.stderr.read()
                code = proc.wait(timeout=30)
            finally:
                proc.kill()
                proc.wait()
                proc.stderr.close()
        self.assertEqual(first, '{"i":1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
