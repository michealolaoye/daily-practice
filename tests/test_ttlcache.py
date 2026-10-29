import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ttlcache import TTLCache  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BasicTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = TTLCache(ttl=10, clock=self.clock)

    def test_set_and_get(self):
        self.cache.set("a", 1)
        self.assertEqual(self.cache.get("a"), 1)

    def test_missing_key_returns_default(self):
        self.assertIsNone(self.cache.get("nope"))
        self.assertEqual(self.cache.get("nope", "dflt"), "dflt")

    def test_falsy_values_are_cached(self):
        for value in (0, "", None, False, []):
            self.cache.set("k", value)
            self.assertIn("k", self.cache)
            self.assertEqual(self.cache.get("k", "sentinel"), value)

    def test_overwrite_replaces_value(self):
        self.cache.set("a", 1)
        self.cache.set("a", 2)
        self.assertEqual(self.cache.get("a"), 2)
        self.assertEqual(len(self.cache), 1)


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = TTLCache(ttl=10, clock=self.clock)

    def test_alive_just_before_ttl(self):
        self.cache.set("a", 1)
        self.clock.advance(9.99)
        self.assertEqual(self.cache.get("a"), 1)

    def test_expired_exactly_at_ttl(self):
        self.cache.set("a", 1)
        self.clock.advance(10)
        self.assertIsNone(self.cache.get("a"))
        self.assertNotIn("a", self.cache)

    def test_overwrite_restarts_the_clock(self):
        self.cache.set("a", 1)
        self.clock.advance(8)
        self.cache.set("a", 2)
        self.clock.advance(8)
        self.assertEqual(self.cache.get("a"), 2)

    def test_reading_does_not_extend_life(self):
        self.cache.set("a", 1)
        self.clock.advance(6)
        self.cache.get("a")
        self.clock.advance(5)
        self.assertIsNone(self.cache.get("a"))

    def test_per_entry_ttl_overrides_default(self):
        self.cache.set("short", 1, ttl=2)
        self.cache.set("long", 2, ttl=100)
        self.clock.advance(5)
        self.assertNotIn("short", self.cache)
        self.assertIn("long", self.cache)

    def test_ttl_none_makes_one_entry_permanent(self):
        self.cache.set("forever", 1, ttl=None)
        self.clock.advance(10 ** 9)
        self.assertEqual(self.cache.get("forever"), 1)

    def test_cache_with_no_default_ttl_never_expires(self):
        cache = TTLCache(clock=self.clock)
        cache.set("a", 1)
        self.clock.advance(10 ** 9)
        self.assertEqual(cache.get("a"), 1)

    def test_len_and_keys_ignore_expired(self):
        self.cache.set("a", 1, ttl=1)
        self.cache.set("b", 2)
        self.clock.advance(2)
        self.assertEqual(len(self.cache), 1)
        self.assertEqual(self.cache.keys(), ["b"])

    def test_purge_counts_removed(self):
        self.cache.set("a", 1, ttl=1)
        self.cache.set("b", 2, ttl=1)
        self.cache.set("c", 3)
        self.clock.advance(5)
        self.assertEqual(self.cache.purge(), 2)
        self.assertEqual(self.cache.purge(), 0)

    def test_ttl_left(self):
        self.cache.set("a", 1)
        self.clock.advance(4)
        self.assertAlmostEqual(self.cache.ttl_left("a"), 6)
        self.cache.set("p", 1, ttl=None)
        self.assertIsNone(self.cache.ttl_left("p"))

    def test_ttl_left_raises_for_missing_or_expired(self):
        with self.assertRaises(KeyError):
            self.cache.ttl_left("nope")
        self.cache.set("a", 1)
        self.clock.advance(10)
        with self.assertRaises(KeyError):
            self.cache.ttl_left("a")


class MaxsizeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_evicts_oldest_written_when_full(self):
        cache = TTLCache(maxsize=2, clock=self.clock)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        self.assertEqual(cache.keys(), ["b", "c"])
        self.assertEqual(cache.evictions, 1)

    def test_rewriting_a_key_makes_it_newest(self):
        cache = TTLCache(maxsize=2, clock=self.clock)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)
        cache.set("c", 3)
        self.assertEqual(cache.keys(), ["a", "c"])

    def test_reading_does_not_protect_from_eviction(self):
        cache = TTLCache(maxsize=2, clock=self.clock)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")
        cache.set("c", 3)
        self.assertNotIn("a", cache)

    def test_expired_entries_are_dropped_before_live_ones_are_evicted(self):
        cache = TTLCache(maxsize=2, clock=self.clock)
        cache.set("old", 1, ttl=1)
        cache.set("keep", 2, ttl=100)
        self.clock.advance(5)
        cache.set("new", 3, ttl=100)
        self.assertEqual(sorted(cache.keys()), ["keep", "new"])
        self.assertEqual(cache.evictions, 0)
        self.assertEqual(cache.expirations, 1)

    def test_maxsize_must_be_positive(self):
        with self.assertRaises(ValueError):
            TTLCache(maxsize=0)


class ValidationTest(unittest.TestCase):
    def test_bad_ttl_values(self):
        for bad in (0, -1, float("nan")):
            with self.subTest(ttl=bad):
                with self.assertRaises(ValueError):
                    TTLCache(ttl=bad)
        cache = TTLCache(ttl=5)
        with self.assertRaises(ValueError):
            cache.set("a", 1, ttl=0)


class StatsAndHelpersTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = TTLCache(ttl=10, clock=self.clock)

    def test_hit_miss_and_expiration_counters(self):
        self.cache.set("a", 1)
        self.cache.get("a")
        self.cache.get("b")
        self.clock.advance(10)
        self.cache.get("a")
        self.assertEqual((self.cache.hits, self.cache.misses), (1, 2))
        self.assertEqual(self.cache.expirations, 1)

    def test_get_or_set_calls_factory_once(self):
        calls = []

        def factory():
            calls.append(1)
            return "v"

        self.assertEqual(self.cache.get_or_set("k", factory), "v")
        self.assertEqual(self.cache.get_or_set("k", factory), "v")
        self.assertEqual(len(calls), 1)

    def test_get_or_set_recomputes_after_expiry(self):
        values = iter(["first", "second"])
        self.assertEqual(self.cache.get_or_set("k", lambda: next(values)), "first")
        self.clock.advance(10)
        self.assertEqual(self.cache.get_or_set("k", lambda: next(values)), "second")

    def test_get_or_set_does_not_cache_a_failing_factory(self):
        def boom():
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            self.cache.get_or_set("k", boom)
        self.assertNotIn("k", self.cache)

    def test_delete(self):
        self.cache.set("a", 1)
        self.assertTrue(self.cache.delete("a"))
        self.assertFalse(self.cache.delete("a"))
        self.cache.set("b", 1)
        self.clock.advance(10)
        self.assertFalse(self.cache.delete("b"))

    def test_clear(self):
        self.cache.set("a", 1)
        self.cache.clear()
        self.assertEqual(len(self.cache), 0)


class ThreadingTest(unittest.TestCase):
    def test_get_or_set_runs_factory_once_across_threads(self):
        cache = TTLCache(ttl=60)
        calls = []
        lock = threading.Lock()

        def factory():
            with lock:
                calls.append(1)
            return "v"

        threads = [threading.Thread(target=cache.get_or_set, args=("k", factory))
                   for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)

    def test_concurrent_writers_respect_maxsize(self):
        cache = TTLCache(maxsize=10)

        def work(n):
            for i in range(200):
                cache.set((n, i), i)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(len(cache), 10)


if __name__ == "__main__":
    unittest.main()
