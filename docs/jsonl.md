# jsonl.py

Filters, picks fields from and counts JSON Lines files (one JSON value per
line). A small `grep` / `cut` / `uniq -c` for logs and API dumps that are
written one object per line. Streams its input. Pure stdlib.

## Why

Structured logs are JSON Lines, and `grep` on them is blunt: `grep 500`
matches a timestamp, a byte count and a status. What I usually want is
"the records where `status` is at least 500", "just these three fields", or
"how many per path". `jq` does all of this but is not installed everywhere I
work (Windows included), and its filter language is more than these three
jobs need.

## API

```python
import jsonl

for number, record in jsonl.records(open("app.log", encoding="utf-8")):
    cond = jsonl.parse_condition("status>=500")
    if jsonl.matches(record, cond):
        print(jsonl.select(record, ["ts", "user.name"]))

jsonl.get_path({"items": [{"id": 7}]}, "items.0.id")   # 7, or jsonl.MISSING
jsonl.count_by(records, "path")                        # [("/b", 3), ("/a", 1)]
```

## CLI usage

```
jsonl.py [-w COND]... [-s PATHS] [-c PATH] [-n N] [--skip-bad] [FILE]

  -w, --where     keep records matching PATH=VALUE (also !=, >, >=, <, <=);
                  repeat to require all of them
  -s, --select    comma-separated paths to keep
  -c, --count-by  count records by this path instead of printing them
  -n, --limit     stop after N matching records
  --skip-bad      skip lines that are not valid JSON, and say how many
```

Paths are dotted: `user.name`, `items.0.id` (digits index a list).

## Real example

```
$ cat app.log
{"ts":1,"status":200,"path":"/a","user":{"name":"ada"}}
{"ts":2,"status":500,"path":"/b","user":{"name":"bob"}}
{"ts":3,"status":503,"path":"/b"}

$ python scripts/jsonl.py -w status\>=500 -s ts,user.name app.log
{"ts":2,"user.name":"bob"}
{"ts":3,"user.name":null}

$ python scripts/jsonl.py -c path app.log
{"value":"/b","count":2}
{"value":"/a","count":1}

$ python scripts/jsonl.py --skip-bad -w user.name=ada app.log
$ python scripts/jsonl.py app.log | head -1      # a closed pipe is fine
```

(Quote or escape `>` and `<` in a shell.)

## Exit codes

- `0` - success, including when downstream closes the pipe (`| head`).
- `1` - a line that is not valid JSON (the message names it; records before
  it have already been printed), or input that is not valid UTF-8.
- `2` - usage error (bad `-w`, `-s` or `-n`, `-c` combined with `-s` or
  `-n`) or the file could not be opened.

## Design notes

- **It streams.** One line is parsed, tested and printed at a time, so a log
  larger than memory is fine, and `-n` stops reading as soon as it has enough.
  The cost is that a bad line half way through is reported after the earlier
  output has gone out; `--skip-bad` is for when that is acceptable.
- **A missing field is not null.** `get_path` returns a `MISSING` marker, so
  `status=null` matches only a real `null`. A missing field is not equal to
  anything, which makes `!=` true for it and every ordering operator false.
- **JSON equality, not Python equality.** In Python `True == 1`; in JSON they
  are different values, so `a=1` does not match `true` (and vice versa), and
  `status=200` does not match the string `"200"`.
- **Ordering only compares like with like**: two numbers or two strings.
  `"10" > 5` is false rather than an error or a surprise, so one odd record
  does not stop a run.
- **Values are read as JSON when they parse.** `status=500` is a number,
  `ok=true` a boolean, `name=bob` the text `bob`. Quote it (`name="500"`)
  to match a string that looks like a number.
- **`-s` always gives the same keys.** A missing field becomes `null`, so
  every output line has the same shape. The key is the path as written
  (`"user.name"`), not a nested object.
- **Output is compact and unescaped**: no spaces, and non-ASCII text is
  written as is, not as `\uXXXX`.
- **Both streams are forced to UTF-8.** JSON Lines is UTF-8, but a Windows
  pipe defaults to the locale code page, which mangles accented text and
  cannot encode most other scripts at all. The tests caught this (see below).
- **A closed pipe is quiet.** On Unix that is `EPIPE`, on Windows `EINVAL`;
  either, from a write, ends the run with exit 0 and nothing on stderr.
- **Counting is by JSON text**, so `1`, `"1"` and `true` are three different
  values, and a list or object is counted by its content.

## Running the tests

```
python -m unittest tests/test_jsonl.py
```

69 tests: path lookup (nested, list index, missing versus null, scalars),
condition parsing, matching (every operator, JSON equality, mixed types,
missing fields), field selection, the record reader (line numbers, blanks,
`--skip-bad`, truncated line, laziness), counting, and the CLI end to end
including exit codes, a directory as input, invalid UTF-8, text outside
the Windows code page, and a reader that hangs up on a large output.

One real bug was caught while writing these: output of `"café"` came back
undecodable on Windows because the pipe used cp1252. The fix is the UTF-8
reconfiguration above.
