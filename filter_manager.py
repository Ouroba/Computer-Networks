"""
URL / domain blacklist and whitelist manager with JSON persistence.

Supports:
  - Exact domain matching  (e.g. "example.com")
  - Wildcard sub-domain matching  (e.g. "*.example.com")
  - Two modes: "blacklist" (block listed domains) or "whitelist" (allow only listed)
  - Runtime add / remove with automatic persistence to data/blacklist.json.

Contributed by: Josephina
"""

import fnmatch
import json
import os
import threading

import config

_DEFAULT_DATA = {
    "mode": "blacklist",
    "blacklist": [],
    "whitelist": [],
}


class FilterManager:
    """Thread-safe blacklist / whitelist filter with JSON persistence."""

    def __init__(self, path: str = config.BLACKLIST_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()



    def is_allowed(self, host: str, url: str = "") -> bool:
        """
        Return True if the request is permitted under current rules.

        In *blacklist* mode: blocked when host matches any blacklist entry.
        In *whitelist* mode: blocked when host does NOT match any whitelist entry.
        """
        with self._lock:
            mode = self._data.get("mode", "blacklist")
            if mode == "blacklist":
                return not self._matches(host, self._data.get("blacklist", []))
            else:
                return self._matches(host, self._data.get("whitelist", []))

    #  Mutation 

    def add_rule(self, rule_type: str, pattern: str):
        """Add a pattern to the blacklist or whitelist."""
        with self._lock:
            lst = self._data.setdefault(rule_type, [])
            if pattern not in lst:
                lst.append(pattern)
            self._save_unlocked()

    def remove_rule(self, rule_type: str, pattern: str):
        """Remove a pattern from the blacklist or whitelist."""
        with self._lock:
            lst = self._data.get(rule_type, [])
            if pattern in lst:
                lst.remove(pattern)
            self._save_unlocked()

    def set_mode(self, mode: str):
        """Switch between 'blacklist' and 'whitelist' mode."""
        if mode not in ("blacklist", "whitelist"):
            return
        with self._lock:
            self._data["mode"] = mode
            self._save_unlocked()

    def get_rules(self) -> dict:
        """Return a copy of the current rules (for admin UI)."""
        with self._lock:
            return json.loads(json.dumps(self._data))

    
    @staticmethod
    def _matches(host: str, patterns: list[str]) -> bool:
        """Check if *host* matches any pattern (supports fnmatch wildcards)."""
        host_lower = host.lower()
        for pat in patterns:
            if fnmatch.fnmatch(host_lower, pat.lower()):
                return True
        return False

    def _load(self):
        """Load rules from the JSON file, or create defaults."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                return
            except (json.JSONDecodeError, OSError):
                pass
        self._data = dict(_DEFAULT_DATA)
        self._save_unlocked()

    def _save_unlocked(self):
        """Persist current rules to disk (caller must hold _lock or be in __init__)."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass


filters = FilterManager()