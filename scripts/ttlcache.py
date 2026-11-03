#!/usr/bin/env python3
"""A small in-memory cache whose entries expire after a time-to-live.

Expired entries are dropped lazily on access (and by `purge()`), so there is
no background thread. An optional `maxsize` evicts the oldest-written entry
when the cache is full. The clock is injectable so expiry can be tested
without sleeping. Thread-safe. Pure stdlib (collections, threading, time)."""

import threading
import time
from collections import OrderedDict

_MISSING = object()


class TTLCache:
    def __init__(self, ttl=None, maxsize=None, clock=time.monotonic):
        """`ttl` is the default lifetime in seconds (None = never expires).
        `maxsize` caps the number of live entries (None = unbounded)."""
        _check_ttl(ttl)
        if maxsize is not None and maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.ttl = ttl
        self.maxsize = maxsize
        self._clock = clock
        self._data = OrderedDict()  # key -> (value, expires_at or None)
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.expirations = 0
        self.evictions = 0

    def set(self, key, value, ttl=_MISSING):
        """Store `value`. A per-call `ttl` overrides the default; pass
        `ttl=None` to make this one entry permanent."""
        ttl = self.ttl if ttl is _MISSING else ttl
        _check_ttl(ttl)
        with self._lock:
            now = self._clock()
            expires = None if ttl is None else now + ttl
            # Re-inserting moves the key to the end, so insertion order is
            # also "last written" order, which is what eviction uses.
            self._data.pop(key, None)
            self._data[key] = (value, expires)
            if self.maxsize is not None and len(self._data) > self.maxsize:
                self._drop_expired(now)
            while self.maxsize is not None and len(self._data) > self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1

    def get(self, key, default=None):
        with self._lock:
            entry = self._live(key)
            if entry is None:
                self.misses += 1
                return default
            self.hits += 1
            return entry[0]

    def get_or_set(self, key, factory, ttl=_MISSING):
        """Return the cached value, or call `factory()` and cache the result.
        The lock is held while `factory` runs, so it is called once per
        missing key even with many threads, but a slow factory blocks
        everyone else."""
        with self._lock:
            entry = self._live(key)
            if entry is not None:
                self.hits += 1
                return entry[0]
            self.misses += 1
            value = factory()
            self.set(key, value, ttl)
            return value

    def delete(self, key):
        """Remove `key`. Returns True if a live entry was removed."""
        with self._lock:
            entry = self._live(key)
            if entry is None:
                return False
            del self._data[key]
            return True

    def ttl_left(self, key):
        """Seconds until `key` expires, `None` if it never does, or
        raises KeyError if it is missing or already expired."""
        with self._lock:
            entry = self._live(key)
            if entry is None:
                raise KeyError(key)
            expires = entry[1]
            return None if expires is None else expires - self._clock()

    def purge(self):
        """Drop every expired entry now. Returns how many were removed."""
        with self._lock:
            return self._drop_expired(self._clock())

    def clear(self):
        with self._lock:
            self._data.clear()

    def keys(self):
        with self._lock:
            self._drop_expired(self._clock())
            return list(self._data)

    def __contains__(self, key):
        with self._lock:
            return self._live(key) is not None

    def __len__(self):
        with self._lock:
            self._drop_expired(self._clock())
            return len(self._data)

    def _live(self, key):
        """The (value, expires) entry for `key`, or None. Drops it if it has
        expired. Caller holds the lock."""
        entry = self._data.get(key)
        if entry is None:
            return None
        expires = entry[1]
        if expires is not None and self._clock() >= expires:
            del self._data[key]
            self.expirations += 1
            return None
        return entry

    def _drop_expired(self, now):
        dead = [k for k, (_, exp) in self._data.items() if exp is not None and now >= exp]
        for key in dead:
            del self._data[key]
        self.expirations += len(dead)
        return len(dead)


def _check_ttl(ttl):
    if ttl is not None and not ttl > 0:
        raise ValueError("ttl must be a positive number of seconds or None")
