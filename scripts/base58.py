#!/usr/bin/env python3
"""Base58 and Base58Check: the encoding Bitcoin addresses are written in.

Base58 reads a byte string as one big number and writes it in base 58,
using an alphabet with the look-alike characters 0, O, I and l left out. A
number has no leading zeros, so each leading zero *byte* is written as a
leading "1" (the first character of the alphabet) to keep the length.
Base58Check appends the first 4 bytes of a double SHA-256 as a checksum, so
a mistyped character is caught instead of becoming a different valid
address. The decoder is strict: nothing outside the alphabet, no
whitespace, and a checksum must match. Pure stdlib."""

import argparse
import hashlib
import hmac
import sys

BITCOIN = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
FLICKR = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
ALPHABETS = {"bitcoin": BITCOIN, "flickr": FLICKR}
CHECK_LEN = 4
# Converting is quadratic in the length, so inputs are capped.
MAX_BYTES = 1 << 16


class Base58Error(ValueError):
    """Raised for text that is not valid Base58 / Base58Check."""


def _alphabet(name):
    try:
        return ALPHABETS[name]
    except KeyError:
        raise ValueError(f"unknown alphabet {name!r} (use one of {', '.join(ALPHABETS)})") from None


def encode(data, alphabet="bitcoin"):
    chars = _alphabet(alphabet)
    data = bytes(data)
    if len(data) > MAX_BYTES:
        raise ValueError(f"input is {len(data)} bytes, the limit is {MAX_BYTES}")
    zeros = len(data) - len(data.lstrip(b"\0"))
    n = int.from_bytes(data, "big")
    digits = []
    while n:
        n, r = divmod(n, 58)
        digits.append(chars[r])
    return chars[0] * zeros + "".join(reversed(digits))


def decode(text, alphabet="bitcoin"):
    chars = _alphabet(alphabet)
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_BYTES * 2:
        raise Base58Error(f"input is {len(text)} characters, the limit is {MAX_BYTES * 2}")
    n = 0
    for pos, ch in enumerate(text):
        v = chars.find(ch)
        if v < 0:
            hint = " (0, O, I and l are left out on purpose)" if ch in "0OIl" else ""
            raise Base58Error(f"invalid character {ch!r} at position {pos}{hint}")
        n = n * 58 + v
    zeros = len(text) - len(text.lstrip(chars[0]))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * zeros + body


def _checksum(payload):
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:CHECK_LEN]


def encode_check(payload, alphabet="bitcoin"):
    payload = bytes(payload)
    return encode(payload + _checksum(payload), alphabet)


def decode_check(text, alphabet="bitcoin"):
    raw = decode(text, alphabet)
    if len(raw) < CHECK_LEN:
        raise Base58Error(f"too short for a checksum ({len(raw)} bytes)")
    payload, check = raw[:-CHECK_LEN], raw[-CHECK_LEN:]
    if not hmac.compare_digest(check, _checksum(payload)):
        raise Base58Error("checksum mismatch: a character was mistyped or the text is cut short")
    return payload


def _parse_hex(text):
    text = text.strip()
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise Base58Error(f"not valid hex: {text[:20]!r}") from None


def main(argv=None, stdin=None, stdout=None, stderr=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    stderr = stderr or sys.stderr
    p = argparse.ArgumentParser(description="Base58 encode stdin or FILE (-d to decode).")
    p.add_argument("file", nargs="?")
    p.add_argument("-d", "--decode", action="store_true")
    p.add_argument("--check", action="store_true", help="Base58Check: add / verify a 4-byte checksum")
    p.add_argument("--flickr", action="store_true", help="use the Flickr alphabet (lower case first)")
    p.add_argument("--hex", action="store_true", help="bytes are written as hex instead of raw")
    args = p.parse_args(argv)
    alphabet = "flickr" if args.flickr else "bitcoin"
    try:
        if args.file and args.file != "-":
            with open(args.file, "rb") as f:
                data = f.read()
        else:
            data = stdin.read()
    except OSError as err:
        print(f"base58: {err}", file=stderr)
        return 2
    try:
        if args.decode:
            text = data.decode("ascii").strip() if data.isascii() else None
            if text is None:
                raise Base58Error("input is not ASCII")
            raw = decode_check(text, alphabet) if args.check else decode(text, alphabet)
            stdout.write(raw.hex().encode() + b"\n" if args.hex else raw)
        else:
            raw = _parse_hex(data.decode("ascii", "replace")) if args.hex else data
            out = encode_check(raw, alphabet) if args.check else encode(raw, alphabet)
            stdout.write(out.encode() + b"\n")
    except (Base58Error, ValueError) as err:
        print(f"base58: {err}", file=stderr)
        return 1
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
