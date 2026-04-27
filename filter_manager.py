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

# Default structure for the rules on first startup
_DEFAULT_DATA = {
    "mode": "blacklist",  # Default mode: "blacklist" blocks, "whitelist" allows
    "blacklist": [],  # Empty blacklist (to be populated later)
    "whitelist": [],  # Empty whitelist (to be populated later)
}

class FilterManager:
    """Thread-safe filter manager for blacklist/whitelist with JSON persistence."""

    def __init__(self, path: str = config.BLACKLIST_FILE):
        """Initialize the filter manager and load the rules from disk."""
        self._path = path  # Path to the rules file
        self._lock = threading.Lock()  # Ensures thread safety when modifying rules
        self._data = {}  # Stores the current rules
        self._load()  # Load the rules from the JSON file

    def is_allowed(self, host: str, url: str = "") -> bool:
        """Check if the request is allowed based on current rules."""
        with self._lock:
            mode = self._data.get("mode", "blacklist")
            if mode == "blacklist":
                # In blacklist mode, block if the host matches any pattern in the blacklist
                return not self._matches(host, self._data.get("blacklist", []))
            else:
                # In whitelist mode, only allow if the host matches the whitelist
                return self._matches(host, self._data.get("whitelist", []))

    def add_rule(self, rule_type: str, pattern: str):
        """Add a domain pattern to the blacklist or whitelist at runtime."""
        with self._lock:
            lst = self._data.setdefault(rule_type, [])
            if pattern not in lst:
                lst.append(pattern)
            self._save_unlocked()  # Persist the updated rules to disk

    def remove_rule(self, rule_type: str, pattern: str):
        """Remove a domain pattern from the blacklist or whitelist."""
        with self._lock:
            lst = self._data.get(rule_type, [])
            if pattern in lst:
                lst.remove(pattern)
            self._save_unlocked()  # Persist the updated rules to disk

    def set_mode(self, mode: str):
        """Switch between 'blacklist' and 'whitelist' filtering modes."""
        if mode not in ("blacklist", "whitelist"):
            return  # Ignore invalid mode values
        with self._lock:
            self._data["mode"] = mode
            self._save_unlocked()  # Save the new mode

    def get_rules(self) -> dict:
        """Return a copy of the current rules for display in the admin panel."""
        with self._lock:
            return json.loads(json.dumps(self._data))  # Deep copy to avoid modification

    # Internal helper methods
    @staticmethod
    def _matches(host: str, patterns: list[str]) -> bool:
        """Check if the host matches any of the given patterns using fnmatch."""
        host_lower = host.lower()
        for pat in patterns:
            if fnmatch.fnmatch(host_lower, pat.lower()):
                return True
        return False  # No match found

    def _load(self):
        """Load filtering rules from the JSON file on disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)  # Ensure directory exists
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                return  # Successfully loaded rules
            except (json.JSONDecodeError, OSError):
                pass  # Ignore errors and fall back to default rules
        # File not found or unreadable — use default rules and write to disk
        self._data = _DEFAULT_DATA.copy()
        self._save_unlocked()

    def _save_unlocked(self):
        """Save the current rules to the JSON file without acquiring the lock."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass  # Ignore write errors (failure won't crash the proxy)

# Module-level singleton for use across the application
filters = FilterManager()
