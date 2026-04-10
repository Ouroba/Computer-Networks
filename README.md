# Caching Proxy Server

A multi-threaded HTTP/HTTPS caching proxy server built with Python socket programming.

## Features

| Feature | Description |
|---|---|
| **HTTP Proxying** | Forwards client requests to target servers and relays responses back. |
| **HTTPS Tunneling** | Supports `CONNECT` method for secure pass-through tunneling. |
| **HTTPS MITM** (bonus) | Optional man-in-the-middle decryption using a self-signed CA for inspection. |
| **Content Caching** | In-memory LRU cache with TTL derived from `Cache-Control` / `Expires` headers. |
| **Blacklist / Whitelist** | Domain filtering with wildcard support, persisted to JSON. |
| **Logging** | Per-request structured logging to file and in-memory ring buffer. |
| **Multithreading** | One thread per client connection for concurrent handling. |
| **Admin Interface** (bonus) | Web-based dashboard for live stats, log viewing, cache management, and filter editing. |

## Project Structure

```
proxy_server.py       Main entry point — TCP listener, threading, HTTP forwarding
request_parser.py     HTTP request parsing and header manipulation
cache_manager.py      Thread-safe in-memory cache with TTL & LRU eviction
filter_manager.py     Blacklist/whitelist with JSON persistence
ssl_handler.py        HTTPS CONNECT tunneling + MITM decryption
generate_cert.py      Self-signed CA and per-host certificate generation
proxy_logger.py       Structured file + in-memory logging
admin_interface.py    Flask-based admin web UI
config.py             Central configuration constants
templates/admin.html  Admin dashboard HTML template
static/style.css      Admin dashboard styles
data/blacklist.json   Persisted filter rules
certs/                Generated SSL certificates (created at runtime)
logs/proxy.log        Log file output
```

## Prerequisites

- Python 3.10 or later
- pip

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Start the proxy server

```bash
python proxy_server.py
```

This starts:
- **Proxy** on `0.0.0.0:8888`
- **Admin UI** on `http://localhost:8080`

### Configure your browser / client

Set your HTTP proxy to `localhost:8888`. For example with curl:

```bash
# HTTP request through the proxy
curl -x http://localhost:8888 http://httpbin.org/get

# HTTPS request through the proxy (tunneled)
curl -x http://localhost:8888 https://httpbin.org/get
```

### Admin Interface

Open `http://localhost:8080` in your browser to access:
- **Dashboard** — live stats: total requests, cache hit rate, active connections.
- **Logs** — scrollable table of recent requests with filtering.
- **Cache** — inspect cached entries, invalidate individual URLs, or clear everything.
- **Filters** — add/remove blacklist or whitelist patterns, switch filtering mode.

### HTTPS MITM Mode (optional)

1. In `config.py`, set `ENABLE_MITM = True`.
2. Start the proxy. On first run it generates `certs/ca.pem` and `certs/ca.key`.
3. Import `certs/ca.pem` into your browser's trusted certificate store.
4. HTTPS traffic will now be decrypted, cached, and filtered by the proxy.

> **Note:** MITM mode is intended for debugging and educational purposes only.

## Configuration

All settings are in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `PROXY_HOST` | `0.0.0.0` | Address the proxy listens on |
| `PROXY_PORT` | `8888` | Port the proxy listens on |
| `ADMIN_PORT` | `8080` | Port for the admin web UI |
| `CACHE_DEFAULT_TTL` | `300` | Default cache TTL in seconds |
| `MAX_CACHE_SIZE` | `200` | Maximum number of cached entries |
| `BUFFER_SIZE` | `8192` | Socket read buffer size in bytes |
| `SOCKET_TIMEOUT` | `30` | Socket timeout in seconds |
| `ENABLE_MITM` | `False` | Enable HTTPS man-in-the-middle decryption |

## Testing

```bash
# 1. Basic HTTP proxy
curl -x http://localhost:8888 http://httpbin.org/get

# 2. HTTPS tunnel
curl -x http://localhost:8888 https://httpbin.org/get

# 3. Caching — repeat the same request; check logs for CACHE-HIT
curl -x http://localhost:8888 http://httpbin.org/get
curl -x http://localhost:8888 http://httpbin.org/get

# 4. Blacklisting — add a domain via admin UI, then try to access it
curl -x http://localhost:8888 http://blocked-domain.com
# Expected: 403 Forbidden

# 5. Concurrency
for i in $(seq 1 20); do curl -x http://localhost:8888 http://httpbin.org/get & done

# 6. Admin UI — open in browser
start http://localhost:8080
```

