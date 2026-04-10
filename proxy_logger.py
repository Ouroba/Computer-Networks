"""
Structured logging for the proxy server.

Provides:
  - File-based logging to logs/proxy.log.
  - In-memory ring buffer of the last N structured entries for the admin UI.
  - Thread-safe access to both stores.

Contributed by: 
"""

import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone

import config

# ── Ensure log directory exists ──────────────────────────────────────────────
os.makedirs(config.LOG_DIR, exist_ok=True)

# ── Standard Python logger (file output) ────────────────────────────────────
_file_logger = logging.getLogger("proxy")
_file_logger.setLevel(logging.INFO)

_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
_handler.setFormatter(
    logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
_file_logger.addHandler(_handler)

# ── In-memory ring buffer for admin UI ───────────────────────────────────────
_ring: deque[dict] = deque(maxlen=config.LOG_RING_SIZE)
_ring_lock = threading.Lock()

# ── Counters ─────────────────────────────────────────────────────────────────
_counters_lock = threading.Lock()
_counters = {
    "total_requests": 0,
    "blocked_requests": 0,
    "active_connections": 0,
}


def log_request(
    *,
    client_ip: str = "",
    client_port: int = 0,
    target_host: str = "",
    target_port: int = 0,
    method: str = "",
    url: str = "",
    status_code: int = 0,
    response_size: int = 0,
    cache_hit: bool = False,
    error_message: str = "",
    blocked: bool = False,
):
    """Record a completed (or failed) request in both the file log and ring buffer."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    entry = {
        "timestamp": timestamp,
        "client_ip": client_ip,
        "client_port": client_port,
        "target_host": target_host,
        "target_port": target_port,
        "method": method,
        "url": url,
        "status_code": status_code,
        "response_size": response_size,
        "cache_hit": cache_hit,
        "error_message": error_message,
        "blocked": blocked,
    }
    print("LOG:", method, url, status_code, blocked)
    # File log — single human-readable line
    parts = [
        f"[{timestamp}]",
        f"{client_ip}:{client_port}",
        f"-> {target_host}:{target_port}",
        method,
        url,
        f"status={status_code}",
        f"size={response_size}",
    ]
    if cache_hit:
        parts.append("CACHE-HIT")
    if blocked:
        parts.append("BLOCKED")
    if error_message:
        parts.append(f"ERROR: {error_message}")
    _file_logger.info("  ".join(parts))

    # Ring buffer
    with _ring_lock:
        _ring.append(entry)

    # Counters
    with _counters_lock:
        _counters["total_requests"] += 1
        if blocked:
            _counters["blocked_requests"] += 1


def get_recent_logs(n: int = 100) -> list[dict]:
    """Return the *n* most recent log entries (newest last)."""
    with _ring_lock:
        items = list(_ring)
    return items[-n:]


# ── Active-connection tracking ───────────────────────────────────────────────

def connection_opened():
    with _counters_lock:
        _counters["active_connections"] += 1


def connection_closed():
    with _counters_lock:
        _counters["active_connections"] = max(0, _counters["active_connections"] - 1)


def get_counters() -> dict:
    """Snapshot of global counters (total_requests, blocked, active)."""
    with _counters_lock:
        return dict(_counters)
