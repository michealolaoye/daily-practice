# bencode.py

Bencode is the encoding inside `.torrent` files. This is a codec for it
with a strict decoder, plus a command that prints a torrent's info-hash.
Pure stdlib.

## Why

The format is tiny (integers, byte strings, lists, dictionaries) and
looks forgiving, but a torrent's identity is the SHA-1 of its `info`
dictionary, so two spellings of the same value are two different
torrents. `i03e`, `i-0e`, unsorted keys and a repeated key all "work" in
a lenient decoder and then hash differently, or hide data from whichever
tool reads the other occurrence. I wanted a decoder that accepts exactly
what an encoder would write, and an info-hash taken from the original
bytes rather than from a re-encoding.

## API

```python
from bencode import encode, decode, decode_at, info_hash, BencodeError

encode({"name": "a.txt", "length": 5})     # b'd6:lengthi5e4:name5:a.txte'
decode(b"d3:cow3:moo4:spam4:eggse")        # {b'cow': b'moo', b'spam': b'eggs'}
decode_at(b"xxi5eyy", 2)                   # (5, 5): the value and where it ended
info_hash(open("x.torrent", "rb").read())  # 40 hex characters
decode(b"i03e")                            # raises BencodeError: leading zero in integer ...
```

Decoded strings and dictionary keys are `bytes` (bencode strings are
bytes; piece hashes aren't text). `encode` takes `int`, `bytes`, `str`
(as UTF-8), lists/tuples and dicts with `bytes` or `str` keys, and refuses
everything else, including `bool`, `float` and `None`. Both directions
raise `BencodeError` (a `ValueError`).

## CLI usage

```
bencode.py [--info-hash | --encode] [FILE]

  --info-hash  print the torrent info-hash instead of the decoded data
  --encode     read JSON, write bencode (bytes on stdout)
```

Reads `FILE` or stdin. The default output is JSON: a string that is valid
UTF-8 stays a string, anything else (such as the `pieces` hashes) becomes
`{"hex": "..."}` so binary is never guessed at.

## Real example

```
$ python scripts/bencode.py example.torrent
{
  "announce": "http://tracker.test/",
  "info": {
    "length": 5,
    "name": "a.txt",
    "piece length": 16384,
    "pieces": {
      "hex": "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
    }
  }
}

$ python scripts/bencode.py --info-hash example.torrent
de3edc1dfa1958affac1dbdc8f34d4d6dac43f00

$ printf 'd1:bi1e1:ai2ee' | python scripts/bencode.py; echo "exit $?"
bencode: key b'a' at offset 7 is out of order
exit 1

$ echo '{"b": [1, "x"], "a": "é"}' | python scripts/bencode.py --encode
d1:a2:é1:bli1e1:xee
```

## Exit codes

- `0` - success.
- `1` - the input isn't valid bencode (or valid JSON, with `--encode`).
- `2` - the file could not be read.

## Design notes

- **Canonical or nothing.** Refused: leading zeros (`i03e`, `01:a`),
  `i-0e`, an empty or non-numeric integer, keys out of order, duplicate
  keys, non-string keys, trailing bytes, truncation. The error names the
  offset.
- **Sorted as raw bytes**, so `B` comes before `a`, and `\xff` after
  everything. A text sort of the keys would give a different torrent.
- **The info-hash slices the original bytes.** `info_hash` walks the top
  level dictionary, remembers where the `info` value starts and ends, and
  hashes that slice, then also checks the *whole* file is valid. Hashing a
  re-encoding gives the same answer for canonical input, but only because
  the decoder was strict; slicing doesn't depend on that.
- **Bounded depth.** 100 levels, either way. A `l` repeated 5000 times is
  a `BencodeError`, not a `RecursionError`.
- **Digits are ASCII digits.** `bytes.isdigit()` is ASCII-only, so
  `٣` (Arabic-Indic three) isn't a digit; a test pins that down
  since `str.isdigit()` would say otherwise.
- **Big integers are fine**, as Python ints; bencode sets no limit.
- Not covered: fetching torrents, v2 (BEP 52) merkle hashing, or checking
  that a decoded dictionary is a *meaningful* torrent.

## Running the tests

```
python -m unittest tests.test_bencode
```

29 tests: the BEP 3 examples both ways, big integers, binary strings,
raw-byte key order, 500 random structures that must round trip byte for
byte, every non-canonical integer, string and key form, truncation and
trailing bytes, the depth limit in both directions, things the encoder
must refuse, info-hash of a built torrent (info anywhere in the file,
rest of the file irrelevant, any change to `info` changes it, bad files
refused), JSON display of binary, and the CLI with exit codes and UTF-8
through a pipe.
