import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from toposort import CycleError, levels, parse_lines, toposort  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "toposort.py")


def valid_order(graph, order):
    """True if every item comes after everything it depends on."""
    position = {node: i for i, node in enumerate(order)}
    return all(position[dep] < position[node] for node, deps in graph.items() for dep in deps)


class OrderTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(toposort({}), [])
        self.assertEqual(levels({}), [])

    def test_single_item(self):
        self.assertEqual(toposort({"a": []}), ["a"])

    def test_chain(self):
        self.assertEqual(toposort({"c": ["b"], "b": ["a"], "a": []}), ["a", "b", "c"])

    def test_items_only_named_as_dependencies_are_included(self):
        self.assertEqual(toposort({"app": ["lib"]}), ["lib", "app"])

    def test_diamond(self):
        graph = {"top": ["left", "right"], "left": ["base"], "right": ["base"], "base": []}
        order = toposort(graph)
        self.assertEqual(order[0], "base")
        self.assertEqual(order[-1], "top")
        self.assertTrue(valid_order(graph, order))

    def test_duplicate_dependencies_are_ignored(self):
        self.assertEqual(toposort({"b": ["a", "a", "a"]}), ["a", "b"])

    def test_disconnected_groups(self):
        graph = {"b": ["a"], "y": ["x"]}
        order = toposort(graph)
        self.assertEqual(sorted(order), ["a", "b", "x", "y"])
        self.assertTrue(valid_order(graph, order))

    def test_accepts_any_iterable_of_dependencies(self):
        graph = {"c": ("a", "b"), "b": {"a"}, "a": iter(())}
        self.assertEqual(toposort(graph), ["a", "b", "c"])

    def test_non_string_items(self):
        self.assertEqual(toposort({3: [2], 2: [1]}), [1, 2, 3])

    def test_does_not_modify_the_input(self):
        graph = {"b": ["a"]}
        toposort(graph)
        self.assertEqual(graph, {"b": ["a"]})

    def test_large_chain_does_not_hit_the_recursion_limit(self):
        n = 5000
        graph = {i: [i - 1] for i in range(1, n)}
        graph[0] = []
        self.assertEqual(toposort(graph), list(range(n)))


class StableOrderTest(unittest.TestCase):
    def test_ties_keep_first_mention_order(self):
        self.assertEqual(toposort({"z": [], "m": [], "a": []}), ["z", "m", "a"])

    def test_same_input_always_gives_the_same_output(self):
        graph = {"x": ["q", "p"], "y": ["p"], "p": [], "q": []}
        self.assertEqual(toposort(graph), toposort(dict(graph)))

    def test_dependency_mentioned_first_sorts_first(self):
        # "b" is first mentioned as a dependency of "a", before "c" appears.
        self.assertEqual(toposort({"a": ["b"], "c": []}), ["b", "c", "a"])


class LevelsTest(unittest.TestCase):
    def test_levels_group_independent_items(self):
        graph = {"app": ["lib", "db"], "lib": ["core"], "db": ["core"], "core": []}
        self.assertEqual(levels(graph), [["core"], ["lib", "db"], ["app"]])

    def test_item_goes_in_the_level_after_its_deepest_dependency(self):
        graph = {"c": ["a", "b"], "b": ["a"], "a": []}
        self.assertEqual(levels(graph), [["a"], ["b"], ["c"]])

    def test_everything_without_dependencies_is_level_zero(self):
        self.assertEqual(levels({"a": [], "b": [], "c": []}), [["a", "b", "c"]])

    def test_flat_order_is_the_levels_joined(self):
        graph = {"app": ["lib", "db"], "lib": ["core"], "db": ["core"]}
        self.assertEqual(toposort(graph), [n for lv in levels(graph) for n in lv])


class CycleTest(unittest.TestCase):
    def assertIsRealCycle(self, graph, cycle):
        self.assertGreaterEqual(len(cycle), 2)
        self.assertEqual(cycle[0], cycle[-1])
        for node, nxt in zip(cycle, cycle[1:]):
            self.assertIn(nxt, graph.get(node, []), f"{node} does not depend on {nxt}")

    def test_self_dependency(self):
        with self.assertRaises(CycleError) as ctx:
            toposort({"a": ["a"]})
        self.assertEqual(ctx.exception.cycle, ["a", "a"])

    def test_two_item_cycle(self):
        graph = {"a": ["b"], "b": ["a"]}
        with self.assertRaises(CycleError) as ctx:
            toposort(graph)
        self.assertIsRealCycle(graph, ctx.exception.cycle)

    def test_longer_cycle_is_reported_in_full(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["a"]}
        with self.assertRaises(CycleError) as ctx:
            toposort(graph)
        self.assertEqual(len(ctx.exception.cycle), 5)
        self.assertIsRealCycle(graph, ctx.exception.cycle)

    def test_reported_cycle_excludes_items_that_merely_lead_into_it(self):
        # "entry" depends on the cycle but is not part of it.
        graph = {"entry": ["a"], "a": ["b"], "b": ["a"]}
        with self.assertRaises(CycleError) as ctx:
            toposort(graph)
        self.assertNotIn("entry", ctx.exception.cycle)
        self.assertIsRealCycle(graph, ctx.exception.cycle)

    def test_reported_cycle_excludes_items_the_cycle_leads_to(self):
        graph = {"a": ["b", "leaf"], "b": ["a"], "leaf": []}
        with self.assertRaises(CycleError) as ctx:
            toposort(graph)
        self.assertNotIn("leaf", ctx.exception.cycle)

    def test_cycle_hidden_behind_acyclic_items(self):
        graph = {"ok1": [], "ok2": ["ok1"], "x": ["y", "ok2"], "y": ["z"], "z": ["x"]}
        with self.assertRaises(CycleError) as ctx:
            toposort(graph)
        self.assertEqual(set(ctx.exception.cycle), {"x", "y", "z"})
        self.assertIsRealCycle(graph, ctx.exception.cycle)

    def test_message_shows_the_path(self):
        with self.assertRaises(CycleError) as ctx:
            toposort({"a": ["b"], "b": ["a"]})
        self.assertIn("a -> b -> a", str(ctx.exception))

    def test_cycle_error_is_a_value_error(self):
        self.assertTrue(issubclass(CycleError, ValueError))

    def test_levels_raises_too(self):
        with self.assertRaises(CycleError):
            levels({"a": ["b"], "b": ["a"]})


class ParseTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_lines(["app: lib db", "lib:", "db: lib"]),
                         {"app": ["lib", "db"], "lib": [], "db": ["lib"]})

    def test_item_without_a_colon_stands_alone(self):
        self.assertEqual(parse_lines(["solo"]), {"solo": []})

    def test_comments_and_blank_lines(self):
        text = ["# build order", "", "a: b   # trailing note", "   ", "b:"]
        self.assertEqual(parse_lines(text), {"a": ["b"], "b": []})

    def test_same_item_on_two_lines_accumulates(self):
        self.assertEqual(parse_lines(["a: b", "a: c"]), {"a": ["b", "c"]})

    def test_extra_whitespace(self):
        self.assertEqual(parse_lines(["  a  :   b    c  "]), {"a": ["b", "c"]})

    def test_empty_item_names_the_line(self):
        with self.assertRaises(ValueError) as ctx:
            parse_lines(["a: b", ": c"])
        self.assertIn("line 2", str(ctx.exception))

    def test_item_with_a_space_in_it_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_lines(["two words: dep"])

    def test_crlf_line_endings(self):
        self.assertEqual(parse_lines(["a: b\r\n", "b:\r\n"]), {"a": ["b"], "b": []})


def run_cli(*args, stdin=""):
    proc = subprocess.run([sys.executable, SCRIPT, *args], input=stdin,
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


class CliTest(unittest.TestCase):
    def test_stdin_one_per_line(self):
        code, out, _ = run_cli(stdin="b: a\na:\n")
        self.assertEqual((code, out), (0, "a\nb\n"))

    def test_levels_flag_puts_each_level_on_a_line(self):
        code, out, _ = run_cli("-l", stdin="app: lib db\nlib: core\ndb: core\n")
        self.assertEqual((code, out), (0, "core\nlib db\napp\n"))

    def test_reads_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deps.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("b: a\n")
            code, out, _ = run_cli(path)
        self.assertEqual((code, out), (0, "a\nb\n"))

    def test_dash_means_stdin(self):
        code, out, _ = run_cli("-", stdin="b: a\n")
        self.assertEqual((code, out), (0, "a\nb\n"))

    def test_cycle_exits_1_and_prints_nothing_on_stdout(self):
        code, out, err = run_cli(stdin="a: b\nb: a\n")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("a -> b -> a", err)

    def test_parse_error_exits_1(self):
        code, _, err = run_cli(stdin=": nothing\n")
        self.assertEqual(code, 1)
        self.assertIn("line 1", err)

    def test_missing_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_cli(os.path.join(tmp, "nope.txt"))
        self.assertEqual(code, 2)
        self.assertIn("toposort:", err)

    def test_unknown_option_and_extra_argument_exit_2(self):
        self.assertEqual(run_cli("--bogus")[0], 2)
        self.assertEqual(run_cli("a.txt", "b.txt")[0], 2)

    def test_empty_input_prints_nothing(self):
        self.assertEqual(run_cli(stdin="")[:2], (0, ""))

    def test_invalid_utf8_exits_1_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.txt")
            with open(path, "wb") as handle:
                handle.write(b"a: \xff\xfe\n")
            code, _, err = run_cli(path)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
