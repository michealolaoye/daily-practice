# base58.py

Base58 and Base58Check, the encoding Bitcoin addresses and wallet keys are
written in. Bitcoin and Flickr alphabets, a strict decoder, and a CLI.
Pure stdlib.

## Why

Base64 is a poor fit for things people read aloud or copy by hand: `0`/`O`
and `I`/`l` look alike, and `+`, `/` and `=` break double-click selection.
Base58 drops the look-alikes and the punctuation. The part worth
understanding is how it treats leading zero bytes (a number has no leading
zeros, so each one is written as a `1`), and how Base58Check adds a
checksum so a typo is an error instead of a different valid address.

## API

```python
from base58 import encode, decode, encode_check, decode_check, Base58Error

encode(b"Hello World!")                  # "2NEpo7TZRRrLZSi2U"
decode("2NEpo7TZRRrLZSi2U")              # b"Hello World!"
encode(b"\0\0a")                         # "112g"  (one "1" per leading zero byte)
encode(b"a", alphabet="flickr")          # "2F"

addr = encode_check(b"\x00" + bytes.fromhex("62e907b15cbf27d5425399ebf6f0fb50ebb88f18"))
# "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"  (the first Bitcoin address ever)
decode_check(addr)                       # the 21 payload bytes, version byte included
decode_check(addr[:-1] + "b")            # raises Base58Error: checksum mismatch ...
```

Bad text raises `Base58Error` (a `ValueError`). A non-string, an unknown
alphabet name, or an input over 64 KiB raises `TypeError` or `ValueError`.

## CLI usage

```
base58.py [-d] [--check] [--flickr] [--hex] [FILE]

  -d        decode (default is encode)
  --check   Base58Check: add a checksum when encoding, verify it when decoding
  --flickr  use the Flickr alphabet (lower case before upper case)
  --hex     the bytes are written as hex text instead of raw
```

Reads `FILE` or stdin. Encoding prints the text and a newline; decoding
writes the bytes (or hex, with `--hex`).

## Real example

```
$ printf 'Hello World!' | python scripts/base58.py
2NEpo7TZRRrLZSi2U

$ printf '0062e907b15cbf27d5425399ebf6f0fb50ebb88f18' | python scripts/base58.py --hex --check
1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa

$ printf '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa' | python scripts/base58.py -d --hex --check
0062e907b15cbf27d5425399ebf6f0fb50ebb88f18

$ printf '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb' | python scripts/base58.py -d --check; echo "exit $?"
base58: checksum mismatch: a character was mistyped or the text is cut short
exit 1

$ printf '1A1zP1eP5QGefi2DMPTfTL5SLmv70' | python scripts/base58.py -d --check; echo "exit $?"
base58: invalid character '0' at position 28 (0, O, I and l are left out on purpose)
exit 1
```

## Exit codes

- `0` - success.
- `1` - invalid text, bad checksum, bad hex, or non-ASCII input to `-d`.
- `2` - the file could not be read.

## Design notes

- **One big number.** The bytes are read as a single integer and divided
  by 58 repeatedly. That is quadratic in the length, which is fine for keys
  and addresses and the reason there is a 64 KiB cap, not a bug.
- **Leading zero bytes are kept as leading `1`s.** `\0\0a` is `112g`, not
  `2g`; without that, `decode(encode(x)) == x` would fail for any value
  with a zero byte at the front, which includes every Bitcoin address.
- **The checksum** is the first 4 bytes of SHA-256 applied twice, compared
  with `hmac.compare_digest`. The version byte is part of the payload; this
  module doesn't interpret it. Checksum errors don't repeat the payload, since
  it may be a private key.
- **Strict decoding.** No whitespace, nothing outside the alphabet; the
  error names the position and notes that `0`, `O`, `I` and `l` are left
  out on purpose. The CLI strips surrounding whitespace so `echo` works.
- **Alphabets aren't interchangeable.** The two have the same 58
  characters in a different order (Flickr puts lower case first), so the
  same text decodes to different bytes; use the same one on both ends.
- **A checksum only catches typos.** One wrong character is always caught
  (a test tries every one at every position of an address), but 4 bytes
  mean a random corruption still slips through about 1 in 4 billion.
- Not covered: Bech32/SegWit addresses (a different encoding and
  checksum), or telling you what a version byte means.

## Running the tests

```
python -m unittest tests.test_base58
```

24 tests: the 12 Bitcoin Core vectors both ways, leading zeros, alphabet
shape (58 distinct characters, no look-alikes), Flickr, 500 random round
trips, every single byte value, every invalid character class, type and size
limits, the genesis address and a WIF private key as Base58Check vectors, the
double SHA-256 checksum computed independently, every single-character typo
at every position of an address, swaps and truncations, short inputs, and the
CLI including exit codes.
