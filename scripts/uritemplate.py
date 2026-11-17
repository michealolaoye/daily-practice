#!/usr/bin/env python3
"""Expand URI Templates (RFC 6570, all four levels).

`{var}`, `{+var}`, `{#var}`, `{.var}`, `{/var}`, `{;var}`, `{?var}`,
`{&var}`, with `:N` prefixes and `*` explode, for strings, lists and
dicts. Malformed templates are an error, not a half-expanded URL.
Pure stdlib."""

import argparse
import json
import re
import sys

UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
RESERVED = frozenset(":/?#[]@!$&'()*+,;=")

# operator: (first, sep, named, ifemp, allow_reserved), as in RFC 6570 Appendix A
OPERATORS = {
    "": ("", ",", False, "", False),
    "+": ("", ",", False, "", True),
    "#": ("#", ",", False, "", True),
    ".": (".", ".", False, "", False),
    "/": ("/", "/", False, "", False),
    ";": (";", ";", True, "", False),
    "?": ("?", "&", True, "=", False),
    "&": ("&", "&", True, "=", False),
}
FUTURE_OPERATORS = frozenset("=,!@|")

_VARCHAR = r"(?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})"
_VARSPEC = re.compile(rf"({_VARCHAR}(?:\.?{_VARCHAR})*)(?::([1-9][0-9]{{0,3}})|(\*))?\Z")
_TRIPLET = re.compile(r"%[0-9A-Fa-f]{2}")


class TemplateError(ValueError):
    """The template is malformed; `position` is the offset of the problem."""

    def __init__(self, message, position):
        super().__init__(f"{message} at position {position}")
        self.position = position


class VarSpec:
    def __init__(self, name, prefix=None, explode=False):
        self.name = name
        self.prefix = prefix
        self.explode = explode

    def __repr__(self):
        mod = f":{self.prefix}" if self.prefix else "*" if self.explode else ""
        return f"VarSpec({self.name}{mod})"


def _encode(text, allow_reserved):
    """Percent-encode `text` as UTF-8, keeping unreserved characters, and with
    `allow_reserved` also reserved characters and existing %XX triplets."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in UNRESERVED or (allow_reserved and c in RESERVED):
            out.append(c)
        elif allow_reserved and c == "%" and _TRIPLET.match(text, i):
            out.append(text[i:i + 3])
            i += 3
            continue
        else:
            out.extend(f"%{b:02X}" for b in c.encode("utf-8", "surrogatepass"))
        i += 1
    return "".join(out)


def _prefix(text, length, allow_reserved):
    """The first `length` characters of `text`. Where triplets pass through
    unencoded, a %XX triplet counts as one character and is never split."""
    if not allow_reserved:
        return text[:length]
    i = 0
    for _ in range(length):
        if i >= len(text):
            break
        i += 3 if text[i] == "%" and _TRIPLET.match(text, i) else 1
    return text[:i]


def _parse_expression(body, start):
    """(operator, [VarSpec]) for the text between the braces at `start`."""
    if not body:
        raise TemplateError("empty expression", start)
    op = body[0] if body[0] in OPERATORS else ""
    if body[0] in FUTURE_OPERATORS:
        raise TemplateError(f"operator {body[0]!r} is reserved for future use", start + 1)
    specs = []
    offset = start + 1 + len(op)
    for raw in body[len(op):].split(","):
        m = _VARSPEC.match(raw)
        if not m:
            raise TemplateError(f"bad variable {raw!r}", offset)
        specs.append(VarSpec(m.group(1), int(m.group(2)) if m.group(2) else None, bool(m.group(3))))
        offset += len(raw) + 1
    return op, specs


def _literal(text):
    if "}" in text:
        raise TemplateError("unmatched '}'", text.index("}"))
    return _encode(text, allow_reserved=True)


def _normalise(name, value):
    """None, a str, a list of str or a list of (key, str) pairs. Members that
    are None are dropped, and a composite with nothing left is undefined."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name}: booleans have no URI form; pass a string")
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, dict):
        pairs = [(str(k), _scalar(name, v)) for k, v in value.items() if v is not None]
        return ("dict", pairs) if pairs else None
    if isinstance(value, (list, tuple)):
        items = [_scalar(name, v) for v in value if v is not None]
        return ("list", items) if items else None
    raise TypeError(f"{name}: can't expand a {type(value).__name__}")


def _scalar(name, value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError(f"{name}: members must be strings or numbers, not {type(value).__name__}")
    return str(value)


class Template:
    """A parsed template; `expand` can be called many times."""

    def __init__(self, template):
        if not isinstance(template, str):
            raise TypeError("template must be a string")
        self.template = template
        self._parts = []  # literal strings and (operator, [VarSpec]) tuples
        pos = 0
        while True:
            open_ = template.find("{", pos)
            if open_ == -1:
                self._parts.append(_literal_at(template, pos, len(template)))
                break
            self._parts.append(_literal_at(template, pos, open_))
            close = template.find("}", open_ + 1)
            nested = template.find("{", open_ + 1)
            if close == -1:
                raise TemplateError("unclosed '{'", open_)
            if nested != -1 and nested < close:
                raise TemplateError("'{' inside an expression", nested)
            self._parts.append(_parse_expression(template[open_ + 1:close], open_))
            pos = close + 1

    @property
    def variables(self):
        """Variable names in order of first appearance."""
        seen = {}
        for part in self._parts:
            if isinstance(part, tuple):
                for spec in part[1]:
                    seen.setdefault(spec.name, None)
        return list(seen)

    def expand(self, variables=None, **kwargs):
        values = dict(variables or {}, **kwargs)
        out = []
        for part in self._parts:
            if isinstance(part, str):
                out.append(part)
            else:
                out.append(_expand_expression(part[0], part[1], values))
        return "".join(out)

    def __repr__(self):
        return f"Template({self.template!r})"


def _literal_at(template, start, end):
    try:
        return _literal(template[start:end])
    except TemplateError as err:
        raise TemplateError("unmatched '}'", start + err.position) from None


def _expand_expression(op, specs, values):
    first, sep, named, ifemp, allow = OPERATORS[op]
    pieces = []
    for spec in specs:
        value = _normalise(spec.name, values.get(spec.name))
        if value is None:
            continue
        pieces.append(_expand_value(spec, value, sep, named, ifemp, allow))
    return first + sep.join(pieces) if pieces else ""


def _expand_value(spec, value, sep, named, ifemp, allow):
    name = _encode(spec.name, True)
    if isinstance(value, str):
        if spec.prefix is not None:
            value = _prefix(value, spec.prefix, allow)
        text = _encode(value, allow)
        if named:
            return name + (ifemp if value == "" else "=" + text)
        return text
    kind, members = value
    if spec.prefix is not None:
        raise TypeError(f"{spec.name}: a :{spec.prefix} prefix only applies to strings")
    if not spec.explode:
        if kind == "list":
            text = ",".join(_encode(m, allow) for m in members)
        else:
            text = ",".join(_encode(k, allow) + "," + _encode(v, allow) for k, v in members)
        return name + "=" + text if named else text
    if kind == "list":
        pairs = [(name, m) for m in members] if named else [(None, m) for m in members]
    else:
        pairs = [(_encode(k, True) if named else _encode(k, allow), v) for k, v in members]
    out = []
    for key, member in pairs:
        if key is None:
            out.append(_encode(member, allow))
        elif named and member == "":
            out.append(key + ifemp)
        else:
            out.append(key + "=" + _encode(member, allow))
    return sep.join(out)


def expand(template, variables=None, **kwargs):
    """Expand `template` with `variables` (a dict) and/or keyword arguments."""
    return Template(template).expand(variables, **kwargs)


def _parse_assignments(pairs):
    """NAME=VALUE strings to a dict; a NAME given more than once is a list."""
    out = {}
    for pair in pairs:
        name, eq, value = pair.partition("=")
        if not eq or not name:
            raise ValueError(f"expected NAME=VALUE, got {pair!r}")
        if name in out:
            prev = out[name]
            out[name] = (prev if isinstance(prev, list) else [prev]) + [value]
        else:
            out[name] = value
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("template", help="the URI template")
    parser.add_argument("assignments", nargs="*", metavar="NAME=VALUE",
                        help="a variable; repeat a NAME to make a list")
    parser.add_argument("-j", "--json", metavar="FILE",
                        help="read variables from a JSON object in FILE (- for stdin); NAME=VALUE overrides it")
    parser.add_argument("--vars", action="store_true", help="print the template's variable names and exit")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        template = Template(args.template)
    except TemplateError as err:
        print(f"uritemplate: {err}", file=sys.stderr)
        return 1
    if args.vars:
        for name in template.variables:
            print(name)
        return 0
    try:
        assigned = _parse_assignments(args.assignments)
    except ValueError as err:
        parser.error(str(err))
    variables = {}
    if args.json:
        try:
            if args.json == "-":
                variables = json.load(sys.stdin)
            else:
                with open(args.json, encoding="utf-8") as fh:
                    variables = json.load(fh)
        except OSError as err:
            print(f"uritemplate: {err}", file=sys.stderr)
            return 2
        except ValueError as err:  # JSONDecodeError and UnicodeDecodeError
            print(f"uritemplate: {args.json}: not valid JSON: {err}", file=sys.stderr)
            return 2
        if not isinstance(variables, dict):
            print(f"uritemplate: {args.json}: expected a JSON object", file=sys.stderr)
            return 2
    variables.update(assigned)
    try:
        print(template.expand(variables))
    except TypeError as err:
        print(f"uritemplate: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
