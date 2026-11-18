import hashlib
import io
import os
import random
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import base58  # noqa: E402
from base58 import BITCOIN, FLICKR, Base58Error, decode, decode_check, encode, encode_check  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "base58.py")


def run(args, data=b""):
    r = subprocess.run([sys.executable, SCRIPT, *args], input=data, capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")


# The vectors from Bitcoin Core's base58_encode_decode.json (hex, text).
CORE = [
    ("", ""),
    ("61", "2g"),
    ("626262", "a3gV"),
    ("636363", "aPEr"),
    ("73696d706c792061206c6f6e6720737472696e67", "2cFupjhnEsSn59qHXstmK2ffpLv2"),
    ("00eb15231dfceb60925886b67d065299925915aeb172c06647", "1NS17iag9jJgTHD1VXjvLCEnZuQ3rJDE9L"),
    ("516b6fcd0f", "ABnLTmg"),
    ("bf4f89001e670274dd", "3SEo3LWLoPntC"),
    ("572e4794", "3EFU7m"),
    ("ecac89cad93923c02321", "EJDM8drfXA6uyA"),
    ("10c8511e", "Rt5zm"),
    ("00000000000000000000", "1111111111"),
]

GENESIS_HASH160 = "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"
GENESIS_ADDRESS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
WIF_KEY = "0c28fca386c7a227600b2fe50b7cae11ec86d3bf1fbe471be89827e19d72aa1d"
WIF = "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"


class Base58(unittest.TestCase):
    def test_bitcoin_core_vectors_both_ways(self):
        for hex_, text in CORE:
            with self.subTest(text=text):
                self.assertEqual(encode(bytes.fromhex(hex_)), text)
                self.assertEqual(decode(text), bytes.fromhex(hex_))

    def test_leading_zero_bytes_become_leading_ones(self):
        for n in range(0, 6):
            data = b"\0" * n + b"\x01"
            self.assertEqual(encode(data), "1" * n + "2")
            self.assertEqual(decode("1" * n + "2"), data)
        self.assertEqual(decode("1"), b"\0")
        self.assertEqual(decode("11"), b"\0\0")

    def test_alphabets_are_permutations_of_each_other_with_no_lookalikes(self):
        for a in (BITCOIN, FLICKR):
            self.assertEqual(len(a), 58)
            self.assertEqual(len(set(a)), 58)
            self.assertFalse(set("0OIl") & set(a))
        self.assertEqual(sorted(BITCOIN), sorted(FLICKR))

    def test_flickr_alphabet(self):
        data = bytes.fromhex("0061626364")
        f = encode(data, "flickr")
        self.assertNotEqual(f, encode(data))
        self.assertEqual(decode(f, "flickr"), data)
        self.assertEqual(encode(b"a", "flickr"), "2F")  # 97 = 2*58 + 39; index 39 is "g" in one alphabet and "F" in the other

    def test_round_trip_random(self):
        rng = random.Random(10)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
            if rng.random() < 0.3:
                data = b"\0" * rng.randrange(1, 5) + data
            for alphabet in ("bitcoin", "flickr"):
                self.assertEqual(decode(encode(data, alphabet), alphabet), data)

    def test_every_single_byte(self):
        seen = set()
        for b in range(256):
            text = encode(bytes([b]))
            self.assertEqual(decode(text), bytes([b]))
            seen.add(text)
        self.assertEqual(len(seen), 256)

    def test_bad_characters_are_named(self):
        for bad in ("0", "O", "I", "l"):
            with self.subTest(bad=bad), self.assertRaisesRegex(Base58Error, "left out on purpose"):
                decode("2" + bad + "2")
        for bad in ("a b", " 2g", "2g\n", "2g=", "é", "١"):
            with self.subTest(bad=bad), self.assertRaisesRegex(Base58Error, "invalid character"):
                decode(bad)

    def test_alphabets_are_not_interchangeable(self):
        self.assertNotEqual(decode("2g"), decode("2g", "flickr"))
        with self.assertRaises(Base58Error):
            decode("0")  # not in either
        with self.assertRaises(ValueError):
            encode(b"x", "crockford")

    def test_type_and_size_errors(self):
        with self.assertRaises(TypeError):
            decode(b"2g")
        with self.assertRaisesRegex(ValueError, "limit"):
            encode(b"\1" * (base58.MAX_BYTES + 1))
        with self.assertRaisesRegex(Base58Error, "limit"):
            decode("2" * (base58.MAX_BYTES * 2 + 1))

    def test_decoding_is_unique_per_value_apart_from_leading_ones(self):
        # "2" and "12" are different (the second has a zero byte in front)
        self.assertNotEqual(decode("2"), decode("12"))


class Base58Check(unittest.TestCase):
    def test_bitcoin_genesis_address(self):
        payload = b"\0" + bytes.fromhex(GENESIS_HASH160)
        self.assertEqual(encode_check(payload), GENESIS_ADDRESS)
        self.assertEqual(decode_check(GENESIS_ADDRESS), payload)

    def test_wif_private_key(self):
        payload = b"\x80" + bytes.fromhex(WIF_KEY)
        self.assertEqual(encode_check(payload), WIF)
        self.assertEqual(decode_check(WIF), payload)

    def test_checksum_is_the_first_four_bytes_of_double_sha256(self):
        payload = b"hello"
        digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()
        self.assertEqual(decode(encode_check(payload)), payload + digest[:4])

    def test_every_single_mistyped_character_is_caught(self):
        for pos in range(len(GENESIS_ADDRESS)):
            for ch in BITCOIN:
                if ch == GENESIS_ADDRESS[pos]:
                    continue
                bad = GENESIS_ADDRESS[:pos] + ch + GENESIS_ADDRESS[pos + 1:]
                with self.assertRaises(Base58Error, msg=bad):
                    decode_check(bad)

    def test_swapped_neighbours_and_truncation_are_caught(self):
        a = GENESIS_ADDRESS
        swapped = a[:5] + a[6] + a[5] + a[7:]
        for bad in (swapped, a[:-1], a + "1", a[1:]):
            with self.subTest(bad=bad), self.assertRaises(Base58Error):
                decode_check(bad)

    def test_too_short_for_a_checksum(self):
        for text in ("", "1", "111", "2g"):
            with self.subTest(text=text), self.assertRaisesRegex(Base58Error, "too short"):
                decode_check(text)

    def test_empty_payload_round_trips(self):
        text = encode_check(b"")
        self.assertEqual(decode_check(text), b"")

    def test_round_trip_random(self):
        rng = random.Random(11)
        for _ in range(300):
            payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
            for alphabet in ("bitcoin", "flickr"):
                self.assertEqual(decode_check(encode_check(payload, alphabet), alphabet), payload)

    def test_checksum_failure_does_not_echo_the_payload(self):
        with self.assertRaises(Base58Error) as cm:
            decode_check(WIF[:-1] + "z")
        self.assertNotIn(WIF[:10], str(cm.exception))


class Cli(unittest.TestCase):
    def test_encode_decode_raw(self):
        code, out, err = run([], b"\0\0hello")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, (encode(b"\0\0hello") + "\n").encode())
        code, out, _ = run(["-d"], out)
        self.assertEqual((code, out), (0, b"\0\0hello"))

    def test_hex_and_check(self):
        code, out, _ = run(["--hex", "--check"], ("00" + GENESIS_HASH160 + "\n").encode())
        self.assertEqual((code, out), (0, (GENESIS_ADDRESS + "\n").encode()))
        code, out, _ = run(["-d", "--hex", "--check"], GENESIS_ADDRESS.encode())
        self.assertEqual((code, out), (0, ("00" + GENESIS_HASH160 + "\n").encode()))

    def test_flickr_flag(self):
        code, out, _ = run(["--flickr"], b"abc")
        self.assertEqual(out.decode().strip(), encode(b"abc", "flickr"))

    def test_exit_codes_and_messages(self):
        code, out, err = run(["-d", "--check"], (GENESIS_ADDRESS[:-1] + "2").encode())
        self.assertEqual((code, out), (1, b""))
        self.assertIn("checksum mismatch", err)
        code, _, err = run(["-d"], b"2g0")
        self.assertEqual(code, 1)
        self.assertIn("left out on purpose", err)
        code, _, err = run(["-d"], "é".encode())
        self.assertEqual(code, 1)
        self.assertIn("not ASCII", err)
        code, _, err = run(["--hex"], b"xyz")
        self.assertEqual(code, 1)
        self.assertIn("not valid hex", err)
        code, _, err = run(["no-such-file"])
        self.assertEqual(code, 2)
        self.assertIn("base58:", err)

    def test_main_with_streams(self):
        out, err = io.BytesIO(), io.StringIO()
        self.assertEqual(base58.main([], stdin=io.BytesIO(b"a"), stdout=out, stderr=err), 0)
        self.assertEqual(out.getvalue(), b"2g\n")


if __name__ == "__main__":
    unittest.main()
