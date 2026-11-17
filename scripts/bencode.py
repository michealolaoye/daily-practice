#!/usr/bin/env python3
"""Bencode, the encoding inside .torrent files, with a strict decoder.

Four types: integers (i42e), byte strings (4:spam), lists (l...e) and
dictionaries (d...e, keys are byte strings in sorted order). The decoder
refuses anything an encoder wouldn't write (leading zeros, i-0e, unsorted
or duplicate keys, trailing bytes), because two spellings of one value
means two different hashes of it. `info_hash` hashes the exact bytes of
the "info" dictionary as they appear in the file, which is what a
torrent's identity is. Pure stdlib."""

import argparse
import hashlib
import json
import sys

MAX_DEPTH = 100


class BencodeError(ValueError):
    """Raised for input that isn't canonical bencode, or can't be encoded."""


def encode(value):
    """Encode int, bytes/str, list/tuple or dict. str becomes UTF-8; dict
    keys must be bytes or str; bool is refused rather than treated as 0/1."""
    out = bytearray()
    _encode(value, out, 0)
    return bytes(out)


def _encode(value, out, depth):
    if depth > MAX_DEPTH:
        raise BencodeError("nested too deeply")
    if isinstance(value, bool):
        raise BencodeError("bool can't be bencoded (use 0 or 1)")
    if isinstance(value, int):
        out += b"i%de" % value
    elif isinstance(value, (bytes, bytearray)):
        out += b"%d:" % len(value) + bytes(value)
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        out += b"%d:" % len(raw) + raw
    elif isinstance(value, (list, tuple)):
        out += b"l"
        for item in value:
            _encode(item, out, depth + 1)
        out += b"e"
    elif isinstance(value, dict):
        keys = {}
        for k, v in value.items():
            if isinstance(k, str):
                k = k.encode("utf-8")
            elif not isinstance(k, (bytes, bytearray)):
                raise BencodeError(f"dict key must be bytes or str, not {type(k).__name__}")
            k = bytes(k)
            if k in keys:
                raise BencodeError(f"duplicate dict key {k!r}")
            keys[k] = v
        out += b"d"
        for k in sorted(keys):  # raw byte order, as the spec says
            _encode(k, out, depth + 1)
            _encode(keys[k], out, depth + 1)
        out += b"e"
    else:
        raise BencodeError(f"can't bencode {type(value).__name__}")


def decode(data):
    """Decode one complete value; trailing bytes are an error."""
    value, end = decode_at(data, 0)
    if end != len(data):
        raise BencodeError(f"{len(data) - end} unexpected bytes after the value at offset {end}")
    return value


def decode_at(data, pos):
    """Decode the value starting at `pos`; returns (value, end offset)."""
    data = bytes(data)
    return _decode(data, pos, 0)


def _read_digits(data, pos, stop, what):
    end = data.find(stop, pos)
    if end < 0:
        raise BencodeError(f"unterminated {what} at offset {pos}")
    text = data[pos:end]
    body = text[1:] if text.startswith(b"-") else text
    if not body.isdigit() or not body.isascii():
        raise BencodeError(f"bad {what} {text!r} at offset {pos}")
    if len(body) > 1 and body.startswith(b"0"):
        raise BencodeError(f"leading zero in {what} {text!r} at offset {pos}")
    if text == b"-0":
        raise BencodeError(f"negative zero at offset {pos}")
    return int(text), end


def _decode(data, pos, depth):
    if depth > MAX_DEPTH:
        raise BencodeError("nested too deeply")
    if pos >= len(data):
        raise BencodeError(f"unexpected end of data at offset {pos}")
    c = data[pos : pos + 1]
    if c == b"i":
        n, end = _read_digits(data, pos + 1, b"e", "integer")
        return n, end + 1
    if c.isdigit():
        n, end = _read_digits(data, pos, b":", "string length")  # starts with a digit, so never negative
        start = end + 1
        if n > len(data) - start:
            raise BencodeError(f"string at offset {pos} claims {n} bytes, {len(data) - start} left")
        return data[start : start + n], start + n
    if c == b"l":
        items = []
        pos += 1
        while True:
            if pos >= len(data):
                raise BencodeError("unterminated list")
            if data[pos : pos + 1] == b"e":
                return items, pos + 1
            item, pos = _decode(data, pos, depth + 1)
            items.append(item)
    if c == b"d":
        result = {}
        last = None
        pos += 1
        while True:
            if pos >= len(data):
                raise BencodeError("unterminated dictionary")
            if data[pos : pos + 1] == b"e":
                return result, pos + 1
            key_at = pos
            if not data[pos : pos + 1].isdigit():
                raise BencodeError(f"dictionary key at offset {pos} must be a string")
            key, pos = _decode(data, pos, depth + 1)
            if last is not None and key == last:
                raise BencodeError(f"duplicate key {key!r} at offset {key_at}")
            if last is not None and key < last:
                raise BencodeError(f"key {key!r} at offset {key_at} is out of order")
            last = key
            result[key], pos = _decode(data, pos, depth + 1)
    raise BencodeError(f"unexpected byte {c!r} at offset {pos}")


def info_hash(data):
    """SHA-1 (hex) of the raw bytes of the top-level "info" value.

    Hashing a re-encoding would give the same answer for canonical input,
    but slicing the original is what the protocol means and doesn't depend
    on the encoder agreeing."""
    data = bytes(data)
    if data[:1] != b"d":
        raise BencodeError("not a dictionary")
    pos = 1
    while data[pos : pos + 1] != b"e":
        key, pos = _decode(data, pos, 1)
        start = pos
        _, pos = _decode(data, pos, 1)
        if key == b"info":
            decode(data)  # the whole file must still be valid
            return hashlib.sha1(data[start:pos]).hexdigest()
    raise BencodeError('no "info" key')


def to_jsonable(value):
    """For display: bytes that are valid UTF-8 become strings, anything else
    becomes {"hex": "..."} so binary (piece hashes) stays unambiguous."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"hex": value.hex()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k.decode("utf-8", "replace"): to_jsonable(v) for k, v in value.items()}
    return value


def main(argv=None, stdin=None, stdout=None, stderr=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    p = argparse.ArgumentParser(description="Decode bencode (e.g. a .torrent file) to JSON.")
    p.add_argument("file", nargs="?", help="input file (default: stdin)")
    p.add_argument("--info-hash", action="store_true", help="print the torrent info-hash instead")
    p.add_argument("--encode", action="store_true", help="read JSON, write bencode to stdout")
    args = p.parse_args(argv)
    try:
        if args.file and args.file != "-":
            with open(args.file, "rb") as f:
                data = f.read()
        else:
            data = stdin.read()
    except OSError as err:
        print(f"bencode: {err}", file=stderr)
        return 2
    try:
        if args.encode:
            out = encode(json.loads(data.decode("utf-8")))
            (stdout.buffer if hasattr(stdout, "buffer") else stdout).write(out)
        elif args.info_hash:
            print(info_hash(data), file=stdout)
        else:
            json.dump(to_jsonable(decode(data)), stdout, indent=2, ensure_ascii=False)
            print(file=stdout)
    except (BencodeError, ValueError) as err:
        print(f"bencode: {err}", file=stderr)
        return 1
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    sys.exit(main())
