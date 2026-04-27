"""
Thread-safe in-memory response cache with TTL and Cache-Control support.

Features:
  - Stores full HTTP response bytes keyed by URL.
  - Respects Cache-Control (max-age, no-store, no-cache) and Expires headers.
  - LRU-style eviction when MAX_CACHE_SIZE is exceeded.
  - Exposes stats (hits, misses, size) for the admin interface.

Contributed by: Josephina Sakr (Team Member 2)
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
    """LRU cache of HTTP responses with header-driven TTL."""

    def __init__(self, default_ttl: int = config.CACHE_DEFAULT_TTL,
                 max_size: int = config.MAX_CACHE_SIZE):
        # OrderedDict stores cached entries in insertion order.
        # Each key is a URL string; each value is a dict holding the response
        # bytes, headers, timestamps, TTL, and size.
        self._store: OrderedDict[str, dict] = OrderedDict()

        # Lock used to prevent race conditions when multiple threads read/write
        # the cache at the same time (thread safety).
        self._lock = threading.Lock()

        # Default TTL in seconds — used when no Cache-Control or Expires header
        # is found in the response. Value comes from config.py.
        self._default_ttl = default_ttl

        # Maximum number of entries allowed in the cache before eviction kicks in.
        # Value comes from config.py.
        self._max_size = max_size

        # Counters to track how often the cache is used successfully (hit)
        # vs how often a request was not found or was expired (miss).
        self._hits = 0
        self._misses = 0

    # ── Public API ───────────────────────────────────────────────────────
    # The four main methods below form the public interface of the cache.
    # All other code in the proxy interacts with the cache through these methods.

    def get(self, url: str) -> bytes | None:
        """
        Return cached response bytes if present and not expired.

        - Acquires the lock before reading to prevent concurrent modification.
        - If the entry exists but is expired, it is deleted and None is returned.
        - If found and valid, the entry is moved to the end of the OrderedDict
          to mark it as most-recently used (important for LRU eviction order).
        """
        with self._lock:
            entry = self._store.get(url)

            # Cache miss — URL not found at all
            if entry is None:
                self._misses += 1
                return None

            # Cache miss — entry exists but has passed its expiry time
            if time.time() > entry["expires_at"]:
                del self._store[url]
                self._misses += 1
                return None

            # Move to end (most-recently used) so it is evicted last
            self._store.move_to_end(url)
            self._hits += 1
            return entry["response"]

    def put(self, url: str, response_bytes: bytes, response_headers: dict):
        """
        Store a response in the cache if caching is permitted by its headers.

        - Skips storage entirely when Cache-Control contains no-store or no-cache,
          as required by the HTTP caching specification.
        - Calculates the TTL by reading the response headers (see _resolve_ttl).
        - If the cache is already at max capacity after inserting, the oldest
          entry (front of the OrderedDict) is evicted to make room — this is
          the LRU (Least Recently Used) eviction strategy.
        """
        # Check Cache-Control header before acquiring the lock — if caching is
        # forbidden by the server, we return immediately without storing anything.
        cc = response_headers.get("Cache-Control", "").lower()
        if "no-store" in cc or "no-cache" in cc:
            return  # Server has explicitly forbidden caching this response

        # Determine how many seconds to keep this response before it expires
        ttl = self._resolve_ttl(response_headers)

        with self._lock:
            # If this URL is already cached, move it to the end before updating
            # so that the new entry is treated as most-recently used
            if url in self._store:
                self._store.move_to_end(url)

            # Store the full response along with its metadata
            self._store[url] = {
                "response": response_bytes,      # Raw HTTP response bytes to serve
                "headers": response_headers,     # Original response headers
                "timestamp": time.time(),        # Time this entry was cached
                "expires_at": time.time() + ttl, # Absolute time when this entry expires
                "ttl": ttl,                      # TTL in seconds (for reference)
                "size": len(response_bytes),     # Size in bytes (used in stats)
            }

            # LRU eviction — if cache exceeds max size, remove the oldest entry.
            # popitem(last=False) removes the first (least-recently-used) item
            # from the OrderedDict.
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, url: str):
        """
        Remove a single specific entry from the cache by URL.

        Used when we know a particular page has changed and we don't want to
        serve a stale cached response for it.
        """
        with self._lock:
            self._store.pop(url, None)  # pop with default None avoids KeyError if not found

    def clear(self):
        """
        Flush the entire cache — remove all stored entries at once.

        Can be triggered from the admin panel to force a full cache reset.
        """
        with self._lock:
            self._store.clear()

    # ── Stats / admin helpers ────────────────────────────────────────────
    # These two methods are called by the admin interface to display
    # cache performance and contents in the dashboard.

    def get_stats(self) -> dict:
        """
        Return a summary of cache performance statistics.

        Returns:
            hits        — number of requests served from cache
            misses      — number of requests not found or expired in cache
            hit_rate    — percentage of requests that were cache hits
            entry_count — current number of entries stored in the cache
            total_size  — total size in bytes of all cached responses
        """
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                # Avoid division by zero when no requests have been made yet
                "hit_rate": round(self._hits / total * 100, 1) if total else 0.0,
                "entry_count": len(self._store),
                "total_size": sum(e["size"] for e in self._store.values()),
            }

    def get_entries(self) -> list[dict]:
        """
        Return metadata for every entry currently in the cache.

        Used by the admin panel to display the full list of cached URLs.
        Each entry includes the URL, its size, how many seconds remain before
        it expires, and the time it was originally cached.
        """
        now = time.time()
        with self._lock:
            return [
                {
                    "url": url,
                    "size": entry["size"],
                    # Calculate remaining TTL — clamped to 0 so it never goes negative
                    "ttl_remaining": max(0, int(entry["expires_at"] - now)),
                    # Format the original cache timestamp as a human-readable string
                    "cached_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.gmtime(entry["timestamp"])
                    ),
                }
                for url, entry in self._store.items()
            ]

    # ── Internal helpers ─────────────────────────────────────────────────
    # Private method — only called internally by put(). Not part of the public API.

    def _resolve_ttl(self, headers: dict) -> int:
        """
        Determine the TTL (time-to-live) in seconds from response headers.

        Priority order:
          1. Cache-Control: max-age=X  — most explicit, highest priority
          2. Expires: <date>           — fallback HTTP/1.0 expiry header
          3. CACHE_DEFAULT_TTL        — project default from config.py

        This follows standard HTTP caching behaviour as defined in RFC 7234.
        Reference: https://datatracker.ietf.org/doc/html/rfc7234#section-5.2
        """
        cc = headers.get("Cache-Control", "")

        # Check each directive in the Cache-Control header (comma-separated)
        for directive in cc.split(","):
            directive = directive.strip().lower()
            if directive.startswith("max-age="):
                try:
                    # Extract the number after "max-age=" and use it as TTL
                    return int(directive.split("=", 1)[1])
                except ValueError:
                    pass  # Malformed value — fall through to next method

        # Fall back to the Expires header if no max-age was found
        expires = headers.get("Expires", "")
        if expires:
            try:
                # parsedate_to_datetime comes from Python's email.utils module
                # It parses the HTTP date format (e.g. "Thu, 01 Jan 2026 00:00:00 GMT")
                exp_dt = parsedate_to_datetime(expires)
                remaining = (exp_dt.timestamp() - time.time())
                if remaining > 0:
                    return int(remaining)
            except Exception:
                pass  # Unparseable Expires header — fall through to default

        # No valid caching header found — use the project default TTL
        return self._default_ttl


# =============================================================================
# Module-level singleton
# A single CacheManager instance is shared across the entire proxy application.
# All modules that need to interact with the cache import this object directly.
# =============================================================================
cache = CacheManager()
