# ttlcache.py

A small in-memory cache whose entries expire after a time-to-live, with an
optional size cap. Thread-safe, pure stdlib (`collections`, `threading`,
`time`).

## Why

Most of the time I want to cache something for "a minute or so", such as an
API response, a DNS lookup or a parsed config, and I don't want a stale
value hanging around forever. An LRU cache doesn't help: it evicts on size,
never on age. A TTL cache answers a different question: how old is too old?

## API

```python
from ttlcache import TTLCache

cache = TTLCache(ttl=60, maxsize=1000)

cache.set("user:1", {"name": "Ada"})
cache.set("token", "abc", ttl=5)         # shorter life for this entry
cache.set("pinned", "x", ttl=None)       # never expires

cache.get("user:1")                      # the value, or None
cache.get("missing", "default")          # "default"
cache.get_or_set("k", lambda: expensive())
cache.ttl_left("token")                  # seconds remaining
cache.delete("token")                    # True if it was live
cache.purge()                            # drop all expired now, returns count
```

- `TTLCache(ttl=None, maxsize=None, clock=time.monotonic)` - `ttl` is the
  default lifetime in seconds. `None` means entries never expire.
- `set(key, value, ttl=<default>)` - a per-call `ttl` overrides the default.
- `get`, `get_or_set`, `delete`, `ttl_left`, `purge`, `clear`, `keys`,
  `in`, `len`.
- Counters: `hits`, `misses`, `expirations`, `evictions`.
- `ValueError` for a `ttl` that isn't a positive number (zero, negative and
  NaN are all rejected) and for `maxsize < 1`.

## Design notes

- **Expiry is lazy.** There is no timer thread. An expired entry is dropped
  when something looks at it (`get`, `in`, `len`, `keys`) or when you call
  `purge()`. A cache nobody touches still holds its expired entries, which is
  why `purge()` exists; call it from a periodic job if memory matters.
- **An entry expires at exactly `set time + ttl`, not a moment after.** At
  `ttl=10` it is alive at 9.99 seconds and gone at 10.
- **Reading does not extend an entry's life.** That's the difference between
  a TTL and an idle timeout. Writing the key again restarts the clock.
- **`maxsize` evicts the oldest *written* entry, not the least recently
  read.** Reads don't change order, so it stays cheap and predictable. When
  the cache is full, expired entries are removed first, so a live entry is
  only evicted if there's genuinely no dead weight to drop.
- **The clock is injectable and defaults to `time.monotonic`.** Wall-clock
  time can jump backwards (NTP, daylight saving), which would make entries
  live longer or die early. A fake clock in the tests means no `sleep()`.
- **`get_or_set` holds the lock while the factory runs.** That guarantees one
  call per missing key even with many threads, but a slow factory blocks
  every other operation on the cache. The lock is reentrant, so a factory
  that uses the same cache doesn't deadlock. A factory that raises caches
  nothing.
- **A stored `None` is a real value.** `in` and `ttl_left` check the entry
  itself, not the value, so falsy values (`0`, `""`, `None`) cache correctly.

## Running the tests

```
python -m unittest tests/test_ttlcache.py
```

29 tests, none of which sleep. They cover set/get, defaults, falsy values,
overwriting, expiry exactly at the boundary, rewriting restarting the clock,
reads not extending life, per-entry TTLs and `ttl=None`, `len`/`keys`
ignoring expired entries, `purge`, `ttl_left`, `maxsize` eviction order,
expired entries being dropped before live ones are evicted, validation of
`ttl` and `maxsize`, the hit/miss/expiration counters, `get_or_set` (cached,
recomputed after expiry, a failing factory), `delete`, `clear`, and two
threaded tests (one factory call across 20 threads, and `maxsize` holding
under concurrent writers).
