"""
Thread-safe in-memory response cache with TTL and Cache-Control support.

Features:
  - Stores full HTTP response bytes keyed by URL.
  - Respects Cache-Control (max-age, no-store, no-cache) and Expires headers.
  - LRU-style eviction when MAX_CACHE_SIZE is exceeded.
  - Exposes stats (hits, misses, size) for the admin interface.

Contributed by: Josephina
"""

import threading
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime

import config


class CacheManager:
    """LRU cache of HTTP responses with header-driven TTL."""

    def __init__(self, default_ttl: int = config.CACHE_DEFAULT_TTL,
                 max_size: int = config.MAX_CACHE_SIZE):
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    #  Public API 

    def get(self, url: str) -> bytes | None:
        """Return cached response bytes if present and not expired."""
        with self._lock:
            entry = self._store.get(url)
            if entry is None:
                self._misses += 1
                return None
            if time.time() > entry["expires_at"]:
                del self._store[url]
                self._misses += 1
                return None
            # Move to end (most-recently used)
            self._store.move_to_end(url)
            self._hits += 1
            return entry["response"]

    def put(self, url: str, response_bytes: bytes, response_headers: dict):
        """
        Store a response if caching is permitted by its headers.

        Skips storage when Cache-Control contains no-store or no-cache.
        """
        cc = response_headers.get("Cache-Control", "").lower()
        if "no-store" in cc or "no-cache" in cc:
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
            # Evict oldest if over capacity
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, url: str):
        """Remove a single entry."""
        with self._lock:
            self._store.pop(url, None)

    def clear(self):
        """Flush the entire cache."""
        with self._lock:
            self._store.clear()

    #  Stats / admin helpers 

    def get_stats(self) -> dict:
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
        """List cached URLs with metadata (for admin UI)."""
        now = time.time()
        with self._lock:
            return [
                {
                    "url": url,
                    "size": entry["size"],
                    "ttl_remaining": max(0, int(entry["expires_at"] - now)),
                    "cached_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.gmtime(entry["timestamp"])
                    ),
                }
                for url, entry in self._store.items()
            ]

    #  Internal helpers 

    def _resolve_ttl(self, headers: dict) -> int:
        """Determine TTL from Cache-Control max-age, Expires, or default."""
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

        return self._default_ttl


# Singleton used across the proxy
cache = CacheManager()
