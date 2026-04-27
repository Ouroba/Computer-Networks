"""
URL / domain blacklist and whitelist manager with JSON persistence.

Supports:
  - Exact domain matching  (e.g. "example.com")
  - Wildcard sub-domain matching  (e.g. "*.example.com")
  - Two modes: "blacklist" (block listed domains) or "whitelist" (allow only listed)
  - Runtime add / remove with automatic persistence to data/blacklist.json.

Contributed by: Josephina Sakr
"""

# ── Standard library imports ──────────────────────────────────────────────────

# fnmatch provides Unix shell-style wildcard matching (e.g. *.example.com).
# It is part of the Python standard library — no external installation needed.
# Source: Python standard library — https://docs.python.org/3/library/fnmatch.html
import fnmatch

# json is used to read rules from and write rules to the blacklist.json file.
# Source: Python standard library — https://docs.python.org/3/library/json.html
import json

import os          # Used to check if the JSON file exists and create directories
import threading   # Used to create a Lock for thread-safe access to the rules

import config      # Project config file — holds BLACKLIST_FILE path


# =============================================================================
# Default data structure used when no blacklist.json file exists yet.
# On first startup, this is written to disk so the file is always present.
# =============================================================================
_DEFAULT_DATA = {
    "mode": "blacklist",   # Active mode: "blacklist" blocks listed domains,
                           # "whitelist" allows only listed domains
    "blacklist": [],       # List of domain patterns to block (in blacklist mode)
    "whitelist": [],       # List of domain patterns to allow (in whitelist mode)
}


class FilterManager:
    """Thread-safe blacklist / whitelist filter with JSON persistence."""

    def __init__(self, path: str = config.BLACKLIST_FILE):
        # Path to the JSON file where rules are stored on disk
        self._path = path

        # Lock used to prevent race conditions when multiple threads read or
        # modify the rules at the same time (thread safety)
        self._lock = threading.Lock()

        # Internal dictionary holding the current rules loaded from JSON.
        # Structure: { "mode": str, "blacklist": [str, ...], "whitelist": [str, ...] }
        self._data: dict = {}

        # Load rules from disk immediately on startup
        self._load()

    # ── Core filtering logic ─────────────────────────────────────────────
    # is_allowed() is the main function of this module. Every incoming request
    # passes through this check before anything else happens in the proxy.

    def is_allowed(self, host: str, url: str = "") -> bool:
        """
        Return True if the request is permitted under the current rules.

        In *blacklist* mode:
            A request is BLOCKED if the host matches any pattern in the blacklist.
            Everything else is allowed through.

        In *whitelist* mode:
            A request is ALLOWED only if the host matches a pattern in the whitelist.
            Everything else is blocked by default.

        When a request is blocked, the proxy returns a 403 Forbidden response
        to the client and the request never reaches the web server.
        """
        with self._lock:
            mode = self._data.get("mode", "blacklist")

            if mode == "blacklist":
                # Block if host matches any blacklist pattern; allow otherwise
                return not self._matches(host, self._data.get("blacklist", []))
            else:
                # Allow only if host matches a whitelist pattern; block otherwise
                return self._matches(host, self._data.get("whitelist", []))

    # ── Runtime rule mutation ────────────────────────────────────────────
    # These methods allow the admin panel to update filtering rules on the fly
    # without needing to restart the proxy server.

    def add_rule(self, rule_type: str, pattern: str):
        """
        Add a new pattern to the blacklist or whitelist at runtime.

        The change takes effect immediately for the next incoming request.
        The updated rules are also saved to disk automatically.

        Args:
            rule_type: either "blacklist" or "whitelist"
            pattern:   a domain string, e.g. "bad.com" or "*.bad.com"
        """
        with self._lock:
            # setdefault ensures the list exists even if the key is missing
            lst = self._data.setdefault(rule_type, [])
            # Only add if not already present — avoids duplicate entries
            if pattern not in lst:
                lst.append(pattern)
            # Persist the updated rules to disk immediately
            self._save_unlocked()

    def remove_rule(self, rule_type: str, pattern: str):
        """
        Remove an existing pattern from the blacklist or whitelist at runtime.

        The change takes effect immediately for the next incoming request.
        The updated rules are also saved to disk automatically.

        Args:
            rule_type: either "blacklist" or "whitelist"
            pattern:   the exact pattern string to remove
        """
        with self._lock:
            lst = self._data.get(rule_type, [])
            # Only remove if the pattern actually exists — avoids ValueError
            if pattern in lst:
                lst.remove(pattern)
            # Persist the updated rules to disk immediately
            self._save_unlocked()

    def set_mode(self, mode: str):
        """
        Switch the active filtering mode between 'blacklist' and 'whitelist'.

        This allows the proxy to change its entire filtering behaviour at runtime
        without restarting or editing the JSON file manually.
        """
        if mode not in ("blacklist", "whitelist"):
            return  # Ignore invalid mode values silently
        with self._lock:
            self._data["mode"] = mode
            self._save_unlocked()

    def get_rules(self) -> dict:
        """
        Return a full copy of the current rules for display in the admin panel.

        Returns a deep copy (via json round-trip) so that the caller cannot
        accidentally modify the internal rules dict.
        """
        with self._lock:
            # json.loads(json.dumps(...)) creates a deep copy of the dictionary
            return json.loads(json.dumps(self._data))

    # ── Internal helpers ─────────────────────────────────────────────────
    # Private methods — only used internally. Not part of the public API.

    @staticmethod
    def _matches(host: str, patterns: list[str]) -> bool:
        """
        Check if *host* matches any pattern in the given list.

        Uses Python's fnmatch module for wildcard support.
        For example, the pattern "*.example.com" will match:
            - www.example.com
            - mail.example.com
            - any.subdomain.example.com
        But will NOT match "example.com" itself (no wildcard prefix).

        Both host and patterns are lowercased before comparison to ensure
        case-insensitive matching regardless of how the domain is typed.

        fnmatch reference: https://docs.python.org/3/library/fnmatch.html
        """
        host_lower = host.lower()
        for pat in patterns:
            # fnmatch.fnmatch(name, pattern) returns True if name matches pattern
            if fnmatch.fnmatch(host_lower, pat.lower()):
                return True
        return False  # No pattern matched

    def _load(self):
        """
        Load filtering rules from the JSON file on disk.

        If the file does not exist or is corrupted, default empty rules are used
        and a fresh JSON file is created automatically.
        """
        # Create the directory for the JSON file if it doesn't exist yet
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                return  # Successfully loaded — exit early
            except (json.JSONDecodeError, OSError):
                # File exists but is unreadable or malformed — fall through to defaults
                pass

        # File not found or unreadable — use built-in defaults and write them to disk
        self._data = dict(_DEFAULT_DATA)
        self._save_unlocked()

    def _save_unlocked(self):
        """
        Write the current rules to the JSON file on disk.

        Named '_unlocked' because this method does NOT acquire the lock itself —
        it must only be called from a context that already holds self._lock,
        or from __init__ before any threads are running.

        Errors during writing are silently ignored to avoid crashing the proxy
        over a non-critical persistence failure.
        """
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                # indent=2 makes the JSON file human-readable
                json.dump(self._data, f, indent=2)
        except OSError:
            pass  # Disk write failed — rules remain in memory but won't persist


# =============================================================================
# Module-level singleton
# A single FilterManager instance is shared across the entire proxy application.
# All modules that need to check or modify filtering rules import this object.
# =============================================================================
filters = FilterManager()
