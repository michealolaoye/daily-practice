import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import bencode  # noqa: E402
from bencode import BencodeError, decode, decode_at, encode, info_hash, to_jsonable  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bencode.py")


def run(args, data=b""):
    r = subprocess.run([sys.executable, SCRIPT, *args], input=data, capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")


# Examples from BEP 3 (the BitTorrent spec) plus the obvious neighbours.
SPEC = [
    (b"i3e", 3),
    (b"i-3e", -3),
    (b"i0e", 0),
    (b"4:spam", b"spam"),
    (b"0:", b""),
    (b"l4:spam4:eggse", [b"spam", b"eggs"]),
    (b"le", []),
    (b"d3:cow3:moo4:spam4:eggse", {b"cow": b"moo", b"spam": b"eggs"}),
    (b"d4:spaml1:a1:bee", {b"spam": [b"a", b"b"]}),
    (b"de", {}),
]


class RoundTrip(unittest.TestCase):
    def test_spec_examples_both_ways(self):
        for raw, value in SPEC:
            with self.subTest(raw=raw):
                self.assertEqual(decode(raw), value)
                self.assertEqual(encode(value), raw)

    def test_big_integers(self):
        for n in (2**63, -(2**63) - 1, 10**40):
            self.assertEqual(decode(encode(n)), n)

    def test_binary_strings(self):
        blob = bytes(range(256))
        self.assertEqual(decode(encode(blob)), blob)
        self.assertEqual(encode(blob)[:4], b"256:")

    def test_str_is_utf8_and_dict_keys_may_be_str(self):
        self.assertEqual(encode("é"), b"2:\xc3\xa9")
        self.assertEqual(encode({"b": 1, "a": 2}), b"d1:ai2e1:bi1ee")

    def test_keys_sorted_as_raw_bytes_not_text(self):
        # "B" (0x42) sorts before "a" (0x61); a case-insensitive sort would not
        self.assertEqual(encode({b"a": 1, b"B": 2}), b"d1:Bi2e1:ai1ee")
        self.assertEqual(encode({b"\xff": 1, b"a": 2}), b"d1:ai2e1:\xffi1ee")

    def test_random_structures_round_trip(self):
        rng = random.Random(7)

        def make(depth):
            kind = rng.randrange(4 if depth < 4 else 2)
            if kind == 0:
                return rng.randrange(-(10**6), 10**6)
            if kind == 1:
                return bytes(rng.randrange(256) for _ in range(rng.randrange(8)))
            if kind == 2:
                return [make(depth + 1) for _ in range(rng.randrange(4))]
            return {
                bytes(rng.randrange(256) for _ in range(rng.randrange(1, 4))): make(depth + 1)
                for _ in range(rng.randrange(4))
            }

        for _ in range(500):
            v = make(0)
            raw = encode(v)
            self.assertEqual(decode(raw), v)
            self.assertEqual(encode(decode(raw)), raw)  # byte-for-byte stable

    def test_decode_at_reports_end_offset(self):
        self.assertEqual(decode_at(b"xxi5eyy", 2), (5, 5))
        self.assertEqual(decode_at(b"3:abcZ", 0), (b"abc", 5))


class Strictness(unittest.TestCase):
    def test_refuses_non_canonical_integers(self):
        for bad in (b"i03e", b"i-0e", b"i00e", b"ie", b"i-e", b"i e", b"i+3e", b"i3.0e", b"i1_0e", b"i3"):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                decode(bad)

    def test_refuses_non_ascii_digits(self):
        for bad in ("i٣e".encode(), "٣:abc".encode()):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                decode(bad)

    def test_refuses_bad_strings(self):
        for bad in (b"01:a", b"-1:a", b"3:ab", b"3", b"3:", b"1a:x", b":a", b"99999999999999999999:a"):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                decode(bad)

    def test_short_string_is_named_not_silently_truncated(self):
        with self.assertRaisesRegex(BencodeError, "claims 5 bytes, 3 left"):
            decode(b"l5:abe")

    def test_refuses_unsorted_and_duplicate_keys(self):
        with self.assertRaisesRegex(BencodeError, "out of order"):
            decode(b"d1:bi1e1:ai2ee")
        with self.assertRaisesRegex(BencodeError, "duplicate"):
            decode(b"d1:ai1e1:ai2ee")

    def test_refuses_non_string_keys_and_missing_values(self):
        for bad in (b"di1ei2ee", b"dle0:e", b"d1:a", b"d1:ae"):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                decode(bad)

    def test_refuses_trailing_bytes_and_truncation(self):
        for bad in (b"i1ex", b"4:spamx", b"lee", b"l", b"li1e", b"d", b"", b"x", b"e"):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                decode(bad)

    def test_depth_limit_is_an_error_not_a_crash(self):
        with self.assertRaisesRegex(BencodeError, "too deeply"):
            decode(b"l" * 5000 + b"e" * 5000)
        value = []
        for _ in range(200):
            value = [value]
        with self.assertRaisesRegex(BencodeError, "too deeply"):
            encode(value)
        ok = b"l" * 100 + b"e" * 100
        self.assertEqual(encode(decode(ok)), ok)

    def test_encoder_refuses_what_it_cannot_represent(self):
        for bad in (True, 1.5, None, {1: b"x"}, {"a": 1, b"a": 2}, object()):
            with self.subTest(bad=bad), self.assertRaises(BencodeError):
                encode(bad)

    def test_bytearray_is_accepted(self):
        self.assertEqual(decode(bytearray(b"i5e")), 5)
        self.assertEqual(encode(bytearray(b"ab")), b"2:ab")


def make_torrent(comment=b""):
    pieces = hashlib.sha1(b"hello").digest()
    info = b"d6:lengthi5e4:name5:a.txt12:piece lengthi16384e6:pieces20:" + pieces + b"e"
    # keys must be in sorted order: announce, comment, info
    return info, b"d8:announce20:http://tracker.test/" + comment + b"4:info" + info + b"e"


class InfoHash(unittest.TestCase):
    def test_hashes_the_raw_info_bytes(self):
        info, torrent = make_torrent()
        self.assertEqual(info_hash(torrent), hashlib.sha1(info).hexdigest())

    def test_info_need_not_be_last(self):
        info, _ = make_torrent()
        early = b"d4:info" + info + b"1:xi1ee"
        self.assertEqual(info_hash(early), hashlib.sha1(info).hexdigest())

    def test_is_independent_of_the_rest_of_the_file(self):
        _, a = make_torrent()
        _, b = make_torrent(comment=b"7:comment3:hmm")
        self.assertEqual(info_hash(a), info_hash(b))

    def test_changing_the_info_changes_the_hash(self):
        _, a = make_torrent()
        self.assertNotEqual(info_hash(a), info_hash(a.replace(b"a.txt", b"b.txt")))

    def test_errors(self):
        with self.assertRaisesRegex(BencodeError, "no .info. key"):
            info_hash(b"d1:ai1ee")
        with self.assertRaisesRegex(BencodeError, "not a dictionary"):
            info_hash(b"li1ee")
        _, torrent = make_torrent()
        with self.assertRaises(BencodeError):
            info_hash(torrent + b"junk")  # valid info, but the file isn't
        with self.assertRaises(BencodeError):
            info_hash(torrent[:-1])


class Display(unittest.TestCase):
    def test_binary_is_marked_not_guessed(self):
        self.assertEqual(to_jsonable(b"abc"), "abc")
        self.assertEqual(to_jsonable(b"\xff\x00"), {"hex": "ff00"})
        self.assertEqual(to_jsonable({b"k": [1, b"\xfe"]}), {"k": [1, {"hex": "fe"}]})


class Cli(unittest.TestCase):
    def test_decode_to_json(self):
        code, out, err = run([], b"d3:cow3:moo4:spaml1:a1:bee")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"cow": "moo", "spam": ["a", "b"]})

    def test_info_hash_from_a_file(self):
        info, torrent = make_torrent()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.torrent")
            with open(path, "wb") as f:
                f.write(torrent)
            code, out, _ = run(["--info-hash", path])
        self.assertEqual(code, 0)
        self.assertEqual(out.decode().strip(), hashlib.sha1(info).hexdigest())

    def test_encode_from_json(self):
        code, out, _ = run(["--encode"], json.dumps({"b": [1, "x"], "a": "é"}).encode())
        self.assertEqual(code, 0)
        self.assertEqual(out, b"d1:a2:\xc3\xa91:bli1e1:xee")

    def test_utf8_output_survives_the_pipe(self):
        code, out, _ = run([], encode({"名前": "é"}))
        self.assertEqual(json.loads(out.decode("utf-8")), {"名前": "é"})

    def test_exit_codes(self):
        code, out, err = run([], b"i03e")
        self.assertEqual((code, out), (1, b""))
        self.assertIn("leading zero", err)
        self.assertEqual(run(["--encode"], b"{nope")[0], 1)
        self.assertEqual(run(["--encode"], b"1.5")[0], 1)
        code, _, err = run(["no-such-file"])
        self.assertEqual(code, 2)
        self.assertIn("bencode:", err)

    def test_main_with_streams(self):
        out, err = io.StringIO(), io.StringIO()
        code = bencode.main([], stdin=io.BytesIO(b"i5e"), stdout=out, stderr=err)
        self.assertEqual((code, out.getvalue().strip(), err.getvalue()), (0, "5", ""))


if __name__ == "__main__":
    unittest.main()
