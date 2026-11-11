import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import uritemplate  # noqa: E402
from uritemplate import Template, TemplateError, expand  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "uritemplate.py")

# RFC 6570 section 3.2.1
RFC_VARS = {
    "count": ["one", "two", "three"],
    "dom": ["example", "com"],
    "dub": "me/too",
    "hello": "Hello World!",
    "half": "50%",
    "var": "value",
    "who": "fred",
    "base": "http://example.com/home/",
    "path": "/foo/bar",
    "list": ["red", "green", "blue"],
    "keys": {"semi": ";", "dot": ".", "comma": ","},
    "v": "6",
    "x": "1024",
    "y": "768",
    "empty": "",
    "empty_keys": {},
    "undef": None,
}

# Every example in RFC 6570 sections 3.2.1 to 3.2.9, copied from the RFC text.
RFC_EXAMPLES = """
{count}            one,two,three
{count*}           one,two,three
{/count}           /one,two,three
{/count*}          /one/two/three
{;count}           ;count=one,two,three
{;count*}          ;count=one;count=two;count=three
{?count}           ?count=one,two,three
{?count*}          ?count=one&count=two&count=three
{&count*}          &count=one&count=two&count=three
{var}              value
{hello}            Hello%20World%21
{half}             50%25
O{empty}X          OX
O{undef}X          OX
{x,y}              1024,768
{x,hello,y}        1024,Hello%20World%21,768
{var:3}            val
{var:30}           value
{list}             red,green,blue
{list*}            red,green,blue
{keys}             semi,%3B,dot,.,comma,%2C
{keys*}            semi=%3B,dot=.,comma=%2C
{+var}                value
{+hello}              Hello%20World!
{+half}               50%25
{base}index           http%3A%2F%2Fexample.com%2Fhome%2Findex
{+base}index          http://example.com/home/index
O{+empty}X            OX
O{+undef}X            OX
{+path}/here          /foo/bar/here
up{+path}{var}/here   up/foo/barvalue/here
{+x,hello,y}          1024,Hello%20World!,768
{+path,x}/here        /foo/bar,1024/here
{+path:6}/here        /foo/b/here
{+list}               red,green,blue
{+list*}              red,green,blue
{+keys}               semi,;,dot,.,comma,,
{+keys*}              semi=;,dot=.,comma=,
{#var}             #value
{#hello}           #Hello%20World!
{#half}            #50%25
foo{#empty}        foo#
foo{#undef}        foo
{#x,hello,y}       #1024,Hello%20World!,768
{#path,x}/here     #/foo/bar,1024/here
{#path:6}/here     #/foo/b/here
{#list}            #red,green,blue
{#list*}           #red,green,blue
{#keys}            #semi,;,dot,.,comma,,
{#keys*}           #semi=;,dot=.,comma=,
{.who}             .fred
{.who,who}         .fred.fred
{.half,who}        .50%25.fred
www{.dom*}         www.example.com
X{.var}            X.value
X{.empty}          X.
X{.undef}          X
X{.var:3}          X.val
X{.list}           X.red,green,blue
X{.list*}          X.red.green.blue
X{.keys}           X.semi,%3B,dot,.,comma,%2C
X{.keys*}          X.semi=%3B.dot=..comma=%2C
X{.empty_keys}     X
X{.empty_keys*}    X
{/who}             /fred
{/who,who}         /fred/fred
{/half,who}        /50%25/fred
{/who,dub}         /fred/me%2Ftoo
{/var}             /value
{/var,empty}       /value/
{/var,undef}       /value
{/var,x}/here      /value/1024/here
{/var:1,var}       /v/value
{/list}            /red,green,blue
{/list*}           /red/green/blue
{/list*,path:4}    /red/green/blue/%2Ffoo
{/keys}            /semi,%3B,dot,.,comma,%2C
{/keys*}           /semi=%3B/dot=./comma=%2C
{;who}             ;who=fred
{;half}            ;half=50%25
{;empty}           ;empty
{;v,empty,who}     ;v=6;empty;who=fred
{;v,bar,who}       ;v=6;who=fred
{;x,y}             ;x=1024;y=768
{;x,y,empty}       ;x=1024;y=768;empty
{;x,y,undef}       ;x=1024;y=768
{;hello:5}         ;hello=Hello
{;list}            ;list=red,green,blue
{;list*}           ;list=red;list=green;list=blue
{;keys}            ;keys=semi,%3B,dot,.,comma,%2C
{;keys*}           ;semi=%3B;dot=.;comma=%2C
{?who}             ?who=fred
{?half}            ?half=50%25
{?x,y}             ?x=1024&y=768
{?x,y,empty}       ?x=1024&y=768&empty=
{?x,y,undef}       ?x=1024&y=768
{?var:3}           ?var=val
{?list}            ?list=red,green,blue
{?list*}           ?list=red&list=green&list=blue
{?keys}            ?keys=semi,%3B,dot,.,comma,%2C
{?keys*}           ?semi=%3B&dot=.&comma=%2C
{&who}             &who=fred
{&half}            &half=50%25
{&x,y,empty}       &x=1024&y=768&empty=
{&x,y,undef}       &x=1024&y=768
{&var:3}           &var=val
{&list}            &list=red,green,blue
{&list*}           &list=red&list=green&list=blue
{&keys}            &keys=semi,%3B,dot,.,comma,%2C
{&keys*}           &semi=%3B&dot=.&comma=%2C
"""


def rfc_examples():
    for line in RFC_EXAMPLES.strip().splitlines():
        template, expected = line.split()
        yield template, expected


def run_cli(*args, stdin=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", env=env)
    return proc.returncode, proc.stdout.replace("\r\n", "\n"), proc.stderr


def call_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = uritemplate.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class RfcExamplesTest(unittest.TestCase):
    def test_every_rfc_example(self):
        examples = list(rfc_examples())
        self.assertEqual(len(examples), 110)
        for template, expected in examples:
            with self.subTest(template=template):
                self.assertEqual(expand(template, RFC_VARS), expected)

    def test_rfc_prefix_examples(self):
        # section 2.4.1: the prefix counts characters before encoding
        self.assertEqual(expand("{semi}", semi=";"), "%3B")
        self.assertEqual(expand("{semi:2}", semi=";"), "%3B")
        self.assertEqual(expand("{var:20}", var="value"), "value")


class LiteralTest(unittest.TestCase):
    def test_plain_template_is_unchanged(self):
        self.assertEqual(expand("http://example.com/a/b?c=d#e"), "http://example.com/a/b?c=d#e")
        self.assertEqual(expand(""), "")

    def test_characters_outside_uri_syntax_are_encoded(self):
        self.assertEqual(expand("/a b/<x>/café"), "/a%20b/%3Cx%3E/caf%C3%A9")
        self.assertEqual(expand('/"q"^`|\\'), "/%22q%22%5E%60%7C%5C")

    def test_percent_triplets_pass_through_but_a_lone_percent_is_encoded(self):
        self.assertEqual(expand("/%41%2f"), "/%41%2f")
        self.assertEqual(expand("/100%"), "/100%25")
        self.assertEqual(expand("/%zz"), "/%25zz")


class EncodingTest(unittest.TestCase):
    def test_unicode_is_utf8_percent_encoded(self):
        self.assertEqual(expand("{x}", x="日本"), "%E6%97%A5%E6%9C%AC")
        self.assertEqual(expand("{x}", x="😀"), "%F0%9F%98%80")
        self.assertEqual(expand("{+x}", x="é/ü"), "%C3%A9/%C3%BC")

    def test_simple_expansion_encodes_reserved_characters(self):
        self.assertEqual(expand("{x}", x=":/?#[]@!$&'()*+,;="),
                         "%3A%2F%3F%23%5B%5D%40%21%24%26%27%28%29%2A%2B%2C%3B%3D")

    def test_reserved_expansion_keeps_reserved_and_triplets_only(self):
        self.assertEqual(expand("{+x}", x="a b%20c%zz"), "a%20b%20c%25zz")
        self.assertEqual(expand("{x}", x="%20"), "%2520")

    def test_numbers_become_strings(self):
        self.assertEqual(expand("/p/{n}{?q}", n=3, q=1.5), "/p/3?q=1.5")

    def test_dict_keys_are_encoded(self):
        self.assertEqual(expand("{?k*}", k={"a b": "c d"}), "?a%20b=c%20d")
        self.assertEqual(expand("{k*}", k={"a b": "c d"}), "a%20b=c%20d")


class PrefixTest(unittest.TestCase):
    def test_prefix_counts_characters_not_bytes(self):
        self.assertEqual(expand("{x:2}", x="日本語"), "%E6%97%A5%E6%9C%AC")
        self.assertEqual(expand("{x:1}", x="😀x"), "%F0%9F%98%80")

    def test_prefix_never_splits_a_triplet_in_reserved_expansion(self):
        self.assertEqual(expand("{+x:2}", x="%41%42%43"), "%41%42")
        self.assertEqual(expand("{+x:1}", x="%4"), "%25")
        # in simple expansion % is just a character
        self.assertEqual(expand("{x:2}", x="%41"), "%254")

    def test_prefix_on_a_list_or_dict_is_an_error(self):
        with self.assertRaisesRegex(TypeError, "only applies to strings"):
            expand("{x:2}", x=["a", "b"])
        with self.assertRaisesRegex(TypeError, "only applies to strings"):
            expand("{x:2}", x={"a": "b"})

    def test_prefix_on_an_undefined_composite_is_fine(self):
        self.assertEqual(expand("a{x:2}", x=[]), "a")


class UndefinedTest(unittest.TestCase):
    def test_missing_none_empty_list_and_dict_are_undefined(self):
        for value in (None, [], {}, ()):
            with self.subTest(value=value):
                self.assertEqual(expand("a{?x,y}", x=value, y="1"), "a?y=1")
        self.assertEqual(expand("a{?x}"), "a")

    def test_none_members_are_dropped(self):
        self.assertEqual(expand("{x}", x=["a", None, "b"]), "a,b")
        self.assertEqual(expand("{?x*}", x={"a": None, "b": "1"}), "?b=1")

    def test_a_composite_with_only_none_members_is_undefined(self):
        self.assertEqual(expand("a{?x}", x=[None]), "a")
        self.assertEqual(expand("a{/x*}", x={"k": None}), "a")

    def test_empty_string_is_defined(self):
        self.assertEqual(expand("a{?x}", x=""), "a?x=")
        self.assertEqual(expand("a{x}b", x=""), "ab")

    def test_empty_members_in_named_explode(self):
        self.assertEqual(expand("{?x*}", x=["a", ""]), "?x=a&x=")
        self.assertEqual(expand("{;x*}", x=["a", ""]), ";x=a;x")
        self.assertEqual(expand("{;k*}", k={"a": ""}), ";a")


class BadValueTest(unittest.TestCase):
    def test_booleans_are_refused(self):
        with self.assertRaisesRegex(TypeError, "booleans"):
            expand("{x}", x=True)
        with self.assertRaisesRegex(TypeError, "members must be strings"):
            expand("{x}", x=["a", False])

    def test_nested_composites_are_refused(self):
        with self.assertRaisesRegex(TypeError, "members must be strings"):
            expand("{x}", x=[["a"]])
        with self.assertRaisesRegex(TypeError, "members must be strings"):
            expand("{x}", x={"a": {"b": "c"}})

    def test_other_types_are_refused(self):
        with self.assertRaisesRegex(TypeError, "can't expand a set"):
            expand("{x}", x={"a"})


class TemplateErrorTest(unittest.TestCase):
    def check(self, template, message, position):
        with self.assertRaises(TemplateError) as ctx:
            Template(template)
        self.assertIn(message, str(ctx.exception))
        self.assertEqual(ctx.exception.position, position)

    def test_unclosed_and_unmatched_braces(self):
        self.check("/a/{b", "unclosed '{'", 3)
        self.check("/a/b}", "unmatched '}'", 4)
        self.check("{x}/}", "unmatched '}'", 4)
        self.check("/{a{b}}", "'{' inside an expression", 3)

    def test_empty_expressions_and_variables(self):
        self.check("/{}", "empty expression", 1)
        self.check("{+}", "bad variable ''", 2)
        self.check("{a,}", "bad variable ''", 3)
        self.check("{a,,b}", "bad variable ''", 3)

    def test_bad_variable_names_and_modifiers(self):
        self.check("{a-b}", "bad variable 'a-b'", 1)
        self.check("{a b}", "bad variable 'a b'", 1)
        self.check("{a..b}", "bad variable", 1)
        self.check("{a.}", "bad variable", 1)
        self.check("{x:0}", "bad variable 'x:0'", 1)
        self.check("{x:10000}", "bad variable 'x:10000'", 1)
        self.check("{x:3*}", "bad variable", 1)
        self.check("{x*:3}", "bad variable", 1)
        self.check("{?a,b:x}", "bad variable 'b:x'", 4)

    def test_future_operators_are_refused(self):
        for op in "=,!@|":
            with self.subTest(op=op):
                self.check("{" + op + "x}", "reserved for future use", 1)

    def test_valid_names(self):
        t = Template("{a.b,_9,%41x,x:9999}")
        self.assertEqual(t.variables, ["a.b", "_9", "%41x", "x"])
        self.assertEqual(t.expand({"a.b": "1", "%41x": "2", "x": "3"}), "1,2,3")

    def test_template_must_be_a_string(self):
        with self.assertRaises(TypeError):
            Template(None)


class TemplateObjectTest(unittest.TestCase):
    def test_variables_in_first_appearance_order_without_repeats(self):
        t = Template("/repos/{owner}/{repo}/issues{/number}{?state,owner}")
        self.assertEqual(t.variables, ["owner", "repo", "number", "state"])

    def test_expand_many_times_with_dict_and_keywords(self):
        t = Template("/u/{id}{?tab}")
        self.assertEqual(t.expand({"id": 1}), "/u/1")
        self.assertEqual(t.expand({"id": 1}, tab="posts"), "/u/1?tab=posts")
        self.assertEqual(t.expand(id=2, tab=None), "/u/2")

    def test_github_style_optional_segment(self):
        t = Template("https://api.github.com/repos/{owner}/{repo}/issues{/number}")
        self.assertEqual(t.expand(owner="o", repo="r"), "https://api.github.com/repos/o/r/issues")
        self.assertEqual(t.expand(owner="o", repo="r", number=7), "https://api.github.com/repos/o/r/issues/7")


class MainTest(unittest.TestCase):
    def test_assignments(self):
        code, out, err = call_main("/search{?q,lang}", "q=a b", "lang=en")
        self.assertEqual((code, out, err), (0, "/search?q=a%20b&lang=en\n", ""))

    def test_repeated_name_is_a_list(self):
        code, out, _ = call_main("{?tag*}", "tag=a", "tag=b", "tag=c")
        self.assertEqual((code, out), (0, "?tag=a&tag=b&tag=c\n"))

    def test_value_may_contain_equals(self):
        self.assertEqual(call_main("{?q}", "q=a=b")[1], "?q=a%3Db\n")

    def test_empty_value_is_defined(self):
        self.assertEqual(call_main("{?q}", "q=")[1], "?q=\n")

    def test_bad_assignment_is_a_usage_error(self):
        for bad in ("novalue", "=x"):
            with self.subTest(bad=bad):
                code, _, err = call_main("{x}", bad)
                self.assertEqual(code, 2)
                self.assertIn("expected NAME=VALUE", err)

    def test_vars(self):
        self.assertEqual(call_main("--vars", "/{a}/{b}{?a,c}"), (0, "a\nb\nc\n", ""))

    def test_template_error_exits_1(self):
        code, out, err = call_main("/{a")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("unclosed '{' at position 1", err)

    def test_value_error_exits_1(self):
        code, out, err = call_main("{x:2}", "x=a", "x=b")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("only applies to strings", err)


class JsonFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def write(self, text, name="vars.json"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_json_file_with_lists_and_dicts(self):
        path = self.write(json.dumps({"tags": ["x", "y"], "f": {"a": "1"}, "n": None}))
        self.assertEqual(call_main("{?tags*}{&f*}{&n}", "-j", path)[1], "?tags=x&tags=y&a=1\n")

    def test_assignments_override_json(self):
        path = self.write('{"q": "from json", "lang": "en"}')
        self.assertEqual(call_main("{?q,lang}", "-j", path, "q=cli")[1], "?q=cli&lang=en\n")

    def test_json_booleans_are_a_value_error(self):
        path = self.write('{"x": true}')
        code, _, err = call_main("{x}", "-j", path)
        self.assertEqual(code, 1)
        self.assertIn("booleans", err)

    def test_bad_json_files_exit_2(self):
        cases = [
            (self.write("{nope", "bad.json"), "not valid JSON"),
            (self.write("[1, 2]", "list.json"), "expected a JSON object"),
            (os.path.join(self.dir, "missing.json"), "missing.json"),
        ]
        for path, message in cases:
            with self.subTest(path=path):
                code, out, err = call_main("{x}", "-j", path)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(message, err)

    def test_non_utf8_json_exits_2(self):
        path = os.path.join(self.dir, "latin1.json")
        with open(path, "wb") as fh:
            fh.write('{"x": "café"}'.encode("latin-1"))
        code, _, err = call_main("{x}", "-j", path)
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)


class CliTest(unittest.TestCase):
    def test_end_to_end(self):
        self.assertEqual(run_cli("/users/{name}{?tab}", "name=Jürgen", "tab=posts"),
                         (0, "/users/J%C3%BCrgen?tab=posts\n", ""))

    def test_json_from_stdin(self):
        code, out, _ = run_cli("{/path*}", "-j", "-", stdin='{"path": ["a b", "c"]}')
        self.assertEqual((code, out), (0, "/a%20b/c\n"))

    def test_exit_codes(self):
        self.assertEqual(run_cli("{x")[0], 1)
        self.assertEqual(run_cli("{x}", "oops")[0], 2)
        self.assertEqual(run_cli()[0], 2)


if __name__ == "__main__":
    unittest.main()
