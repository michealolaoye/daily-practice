# uritemplate.py

Expands URI Templates ([RFC 6570](https://www.rfc-editor.org/rfc/rfc6570),
all four levels): `{var}`, `{+var}`, `{#var}`, `{.var}`, `{/var}`,
`{;var}`, `{?var}` and `{&var}`, with `:N` prefixes and `*` explode, for
strings, lists and dicts. Pure stdlib.

## Why

Deploy scripts and API clients build a lot of URLs, and building them with
f-strings is how `?q=a b&c` ends up in a request, or how an optional
segment becomes `/issues/None`. APIs such as GitHub's already hand out URI
templates (`.../issues{/number}`), and a template says which parts are
optional and how each part is encoded. I wanted a small expander that gets
the encoding right and is checked against every example in the RFC.

## API

```python
from uritemplate import Template, expand

expand("/search{?q,lang}", q="a b", lang="en")       # "/search?q=a%20b&lang=en"
expand("/repos/{owner}/{repo}/issues{/number}", owner="o", repo="r")
                                                     # "/repos/o/r/issues"
expand("{?tags*}", tags=["x", "y"])                  # "?tags=x&tags=y"

t = Template("/u/{id}{?tab}")   # parse once, expand many times
t.variables                     # ["id", "tab"]
t.expand({"id": 7}, tab=None)   # "/u/7"
```

- `expand(template, variables=None, **kwargs)` and
  `Template(template).expand(variables=None, **kwargs)`.
- Values: `str`, `int` or `float` (turned into strings), `list`/`tuple`,
  or `dict` (insertion order is kept). `None`, an empty list or an empty
  dict is *undefined* and is left out, along with its operator prefix. An
  empty string is defined: `{?q}` gives `?q=`.
- `TemplateError` (a `ValueError`) for a malformed template, with
  `.position`. `TypeError` for a value that can't be expanded.

## CLI usage

```
uritemplate.py TEMPLATE [NAME=VALUE ...] [-j FILE]
uritemplate.py --vars TEMPLATE

  NAME=VALUE  a string variable; repeating a NAME makes a list
  -j FILE     variables from a JSON object (- for stdin); NAME=VALUE wins
  --vars      print the template's variable names, one per line
```

## Real example

```
$ python scripts/uritemplate.py "https://api.github.com/repos/{owner}/{repo}/issues{/number}{?state,labels}" \
    owner=michealolaoye repo=daily-practice state=open labels=bug
https://api.github.com/repos/michealolaoye/daily-practice/issues?state=open&labels=bug

$ python scripts/uritemplate.py --vars "https://api.github.com/repos/{owner}/{repo}/issues{/number}{?state,labels}"
owner
repo
number
state
labels

$ echo '{"service":"api","checks":["db","cache"],"opts":{"timeout":"5s","verbose":""}}' \
    | python scripts/uritemplate.py "http://localhost:8080/health/{service}{?checks*}{&opts*}" -j -
http://localhost:8080/health/api?checks=db&checks=cache&timeout=5s&verbose=

$ python scripts/uritemplate.py "/x/{a"
uritemplate: unclosed '{' at position 3
```

`number` isn't given in the first example, so `{/number}` disappears along
with its slash.

## Exit codes

- `0` - expanded.
- `1` - the template is malformed, or a value can't be expanded (a boolean,
  a nested list, a prefix on a list).
- `2` - usage error, or the JSON file can't be read, isn't valid JSON or
  isn't an object.

## Design notes

- **One table drives every operator**, as in the RFC's Appendix A: what to
  write first, the separator, whether names are written, what to write
  after a name with an empty value, and whether reserved characters pass
  through. `{?x}` and `{;x}` differ only in that table: `?x=` against `;x`.
- **Encoding.** Everything except unreserved characters is
  percent-encoded as UTF-8. `{+var}` and `{#var}` also keep reserved
  characters (`:/?#[]@!$&'()*+,;=`) and existing `%XX` triplets, but a
  lone `%` still becomes `%25`. Literal text outside expressions follows
  the reserved rules.
- **Prefixes count characters, not bytes.** `{x:2}` of `日本語` is two
  characters, six encoded bytes. In `{+x:N}` a `%XX` triplet counts as one
  character and is never cut in half. A prefix on a list or dict is an
  error: the RFC says it doesn't apply, and silently ignoring it would hide
  a template bug.
- **Undefined means left out.** `None` members are dropped from lists and
  dicts, and a list or dict with nothing left counts as undefined.
- **Malformed templates raise.** The RFC lets an expander return a partial
  result plus an error flag. Here an unclosed `{`, a stray `}`, an empty
  expression, a bad name or modifier, or an operator reserved for future
  use (`= , ! @ |`) is a `TemplateError` naming the position, so a broken
  template never turns into a URL that's almost right.
- **Booleans are refused.** `True` could be `true`, `True` or `1` depending
  on the API, so the caller has to choose.
- **Parse once.** `Template` keeps the parsed parts, so expanding in a loop
  doesn't parse again.

## Running the tests

```
python -m unittest tests/test_uritemplate.py
```

47 tests. The main one runs all 110 examples from RFC 6570 sections 3.2.1
to 3.2.9 against the RFC's own variables. Those rows were extracted from
the RFC text, and the copy in the test is checked to be the same, row for
row. The other tests cover literals, encoding (Unicode, reserved
characters, triplets), prefixes, undefined values, bad values, every kind
of template error, the `Template` object, and the CLI, including JSON
files, stdin and exit codes.
