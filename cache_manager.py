"""
Thread-safe in-memory response cache with TTL and Cache-Control support.

Features:
  - Stores full HTTP response bytes keyed by URL.
  - Respects Cache-Control (max-age, no-store, no-cache) and Expires headers.
  - LRU-style eviction when MAX_CACHE_SIZE is exceeded.
  - Exposes stats (hits, misses, size) for the admin interface.

Contributed by: Josephina Sakr 
"""

# ── Standard library imports ──────────────────────────────────────────────────

import threading          # Used to create a Lock for thread-safe cache access
import time               # Used to get current timestamps and calculate TTL expiry
# OrderedDict is used instead of a regular dict because it remembers insertion
# order, which allows us to efficiently evict the least-recently-used (oldest)
# entry by popping from the front of the dictionary.
# Source: Python standard library — https://docs.python.org/3/library/collections.html#collections.OrderedDict
from collections import OrderedDict

# parsedate_to_datetime converts an HTTP date string (e.g. from the Expires
# header) into a Python datetime object so we can calculate remaining TTL.
# Source: Python standard library — https://docs.python.org/3/library/email.utils.html#email.utils.parsedate_to_datetime
from email.utils import parsedate_to_datetime
import config             # Project config file — holds CACHE_DEFAULT_TTL and MAX_CACHE_SIZE


class CacheManager:
    """LRU cache of HTTP responses with TTL support."""

    def __init__(self, default_ttl: int = config.CACHE_DEFAULT_TTL, max_size: int = config.MAX_CACHE_SIZE):
        self._store: OrderedDict[str, dict] = OrderedDict()  # Stores cached responses
        self._lock = threading.Lock()  # Ensures thread safety
        self._default_ttl = default_ttl  # Default TTL from config
        self._max_size = max_size  # Max cache size from config
        self._hits = 0  # Cache hit counter
        self._misses = 0  # Cache miss counter

    # ── Public API ───────────────────────────────────────────────────────

    def get(self, url: str) -> bytes | None:
        """Return cached response if present and not expired."""
        with self._lock:
            entry = self._store.get(url)
            if entry is None or time.time() > entry["expires_at"]:  # Cache miss or expired
                self._misses += 1
                return None
            self._store.move_to_end(url)  # Mark as recently used
            self._hits += 1
            return entry["response"]

    def put(self, url: str, response_bytes: bytes, response_headers: dict):
        """Store a response in the cache if allowed by headers."""
        cc = response_headers.get("Cache-Control", "").lower()
        if "no-store" in cc or "no-cache" in cc:  # Avoid caching forbidden responses
            return
        ttl = self._resolve_ttl(response_headers)
        with self._lock:
            if url in self._store:
                self._store.move_to_end(url)
            self._store[url] = {
                "response": response_bytes,
                "headers": response_headers,
                "timestamp": time.time(),
                "expires_at": time.time() + ttl,
                "ttl": ttl,
                "size": len(response_bytes),
            }
            # LRU eviction: Remove oldest entry if cache exceeds max size
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, url: str):
        """Remove a specific entry from the cache."""
        with self._lock:
            self._store.pop(url, None)

    def clear(self):
        """Clear the entire cache."""
        with self._lock:
            self._store.clear()

    def get_stats(self) -> dict:
        """Return cache performance statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0.0,
                "entry_count": len(self._store),
                "total_size": sum(e["size"] for e in self._store.values()),
            }

    def get_entries(self) -> list[dict]:
        """Return metadata for every cached entry."""
        now = time.time()
        with self._lock:
            return [
                {
                    "url": url,
                    "size": entry["size"],
                    "ttl_remaining": max(0, int(entry["expires_at"] - now)),
                    "cached_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(entry["timestamp"])),
                }
                for url, entry in self._store.items()
            ]

    # ── Internal helpers ─────────────────────────────────────────────────

    def _resolve_ttl(self, headers: dict) -> int:
        """Determine the TTL from response headers."""
        cc = headers.get("Cache-Control", "")
        for directive in cc.split(","):
            directive = directive.strip().lower()
            if directive.startswith("max-age="):
                try:
                    return int(directive.split("=", 1)[1])
                except ValueError:
                    pass
        expires = headers.get("Expires", "")
        if expires:
            try:
                exp_dt = parsedate_to_datetime(expires)
                remaining = (exp_dt.timestamp() - time.time())
                if remaining > 0:
                    return int(remaining)
            except Exception:
                pass
        return self._default_ttl  # Fallback to default TTL

# Module-level singleton
cache = CacheManager()
