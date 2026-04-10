# Caching Proxy Server — Project Report

**Course:** Computer Networks  
**Semester:** Spring 2026  
**Team Members:** Ourouba Al Sahmarany, Josephina Sakr, Shahd Moughrabi
**Date:** April 26 2026

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [High-Level Approach](#2-high-level-approach)
3. [Architecture Overview](#3-architecture-overview)
4. [Feature Descriptions](#4-feature-descriptions)
   - 4.1 Basic Proxy Functionality
   - 4.2 Socket Programming
   - 4.3 Request Parsing
   - 4.4 Threading
   - 4.5 Logging
   - 4.6 Content Caching
   - 4.7 Blacklist / Whitelist
   - 4.8 HTTPS Proxy (Bonus)
   - 4.9 Admin Interface (Bonus)
5. [Implementation Details](#5-implementation-details)
6. [Testing](#6-testing)
7. [Challenges Faced](#7-challenges-faced)
8. [Conclusion](#8-conclusion)

---

## 1. Introduction

This report documents the design, implementation, and testing of a multi-threaded caching proxy server built entirely in Python. The proxy server sits between a client (such as a web browser or `curl`) and a target web server, forwarding HTTP and HTTPS requests on behalf of the client. Beyond basic forwarding, the proxy implements intelligent response caching, domain-based access filtering, structured logging, and a web-based administration dashboard.

The project fulfills all required features (A through G) as specified in the assignment, plus both bonus features — HTTPS man-in-the-middle interception (H) and a web-based admin interface (I).

---

## 2. High-Level Approach

Our implementation follows a **modular architecture** where each major feature resides in its own Python module. A central configuration file (`config.py`) stores all tuneable parameters so that no magic numbers are scattered across the codebase.

The design principles guiding development were:

- **Separation of concerns:** Each module handles exactly one responsibility (parsing, caching, filtering, logging, SSL, administration).
- **Thread safety:** All shared state is protected by `threading.Lock` to support concurrent client connections.
- **Graceful degradation:** The proxy starts and operates normally even if optional dependencies (e.g., `pyOpenSSL` for MITM) are not installed.
- **Simplicity:** We force `Connection: close` on forwarded requests to keep the implementation straightforward (one request per socket), avoiding the complexity of HTTP keep-alive pipelining.

---

## 3. Architecture Overview

The proxy follows a pipeline architecture. When a client connects, the request flows through the following stages:

```
Client Request
      |
      v
[TCP Socket Listener]  (proxy_server.py)
      |
      v
[Thread Spawned]  (one thread per connection)
      |
      v
[Request Parser]  (request_parser.py)
      |
      v
[Blacklist/Whitelist Filter]  (filter_manager.py)
      |
      +-- Blocked --> 403 Forbidden response
      |
      v  (Allowed)
[CONNECT?] -- Yes --> [SSL Handler]  (ssl_handler.py)
      |                     |
      No               Tunnel or MITM
      |
      v
[Cache Lookup]  (cache_manager.py)
      |
      +-- Hit --> Return cached response
      |
      v  (Miss)
[Forward to Target Server]
      |
      v
[Store in Cache if cacheable]
      |
      v
[Return response to Client]
      |
      v
[Log the request]  (proxy_logger.py)
```

A separate Flask-based web server (`admin_interface.py`) runs in a daemon thread on a different port, providing real-time visibility into all proxy operations.

### File Structure

| File | Responsibility | Contributor |
|---|---|---|
| `config.py` | Central configuration constants | Ourouba |
| `proxy_server.py` | Main entry point, socket listener, threading, forwarding | Ourouba |
| `request_parser.py` | HTTP request parsing and header manipulation | Ourouba |
| `proxy_logger.py` | File-based and in-memory structured logging | Ourouba |
| `admin_interface.py` | Flask web admin dashboard | Ourouba |
| `cache_manager.py` | Thread-safe LRU cache with TTL support | Josephina |
| `filter_manager.py` | Blacklist/whitelist with JSON persistence | Josephina |
| `ssl_handler.py` | HTTPS CONNECT tunneling and MITM decryption | Shahd |
| `generate_cert.py` | Self-signed CA and per-host certificate generation | Shahd |
| `templates/admin.html` | Admin dashboard HTML/JS template | Ourouba |
| `static/style.css` | Admin dashboard CSS styles | Ourouba |

---

## 4. Feature Descriptions

### 4.1 Basic Proxy Functionality (Requirement A)

**Module:** `proxy_server.py`

The proxy accepts HTTP requests from clients and forwards them to the target server specified in the request URL. The target server's response is then relayed back to the client. The proxy supports:

- **HTTP forwarding:** The client sends a request like `GET http://example.com/page HTTP/1.1`. The proxy parses this, opens a TCP connection to `example.com:80`, sends the request (with the URL converted to a relative path), receives the response, and relays it back.
- **HTTPS tunneling:** When the client sends a `CONNECT host:port` request, the proxy establishes a TCP tunnel between the client and the target server. Encrypted bytes pass through without decryption, preserving end-to-end security.
- **HTTPS MITM (bonus):** When enabled via `config.ENABLE_MITM`, the proxy dynamically generates per-host TLS certificates, decrypts the traffic, and applies caching and filtering to HTTPS requests as well.

**Key implementation detail:** We force `Connection: close` on all forwarded requests. This ensures one-request-per-connection semantics, which simplifies buffer management — we simply read until the target closes the connection.

### 4.2 Socket Programming (Requirement B)

**Module:** `proxy_server.py`

The server uses Python's built-in `socket` module to:

1. **Create a TCP listening socket** bound to `0.0.0.0:8888` (configurable), with `SO_REUSEADDR` set to allow quick restarts.
2. **Accept incoming connections** in an infinite loop via `server_sock.accept()`.
3. **Forward data to target servers** by opening a new `socket.AF_INET` / `SOCK_STREAM` connection per request.
4. **Set timeouts** (`socket.settimeout(30)`) on both client and target sockets to prevent threads from hanging indefinitely.
5. **Read data in chunks** of `BUFFER_SIZE` (8192 bytes) to handle responses of any size.

For HTTPS tunneling, the `ssl_handler.py` module uses `select.select()` for efficient bidirectional relay between the client socket and the target socket, avoiding busy-waiting.

### 4.3 Request Parsing (Requirement C)

**Module:** `request_parser.py`

The `parse_request()` function accepts raw bytes from the client and returns a structured dictionary containing:

| Field | Description |
|---|---|
| `method` | HTTP method (GET, POST, CONNECT, etc.) |
| `url` | Original URL from the request line |
| `host` | Target server hostname |
| `port` | Target server port (default 80 for HTTP, 443 for HTTPS) |
| `path` | Resource path (extracted from absolute URL) |
| `headers` | Dictionary of all request headers |
| `body` | Request body as bytes |
| `version` | HTTP version string (e.g., `HTTP/1.1`) |

The parser handles three URL formats:
- **Absolute URLs:** `GET http://example.com/path` — the hostname and port are extracted from the URL itself.
- **Relative URLs:** `GET /path` — the hostname is extracted from the `Host` header.
- **CONNECT requests:** `CONNECT example.com:443` — the host and port are parsed from the target address.

The `rebuild_request()` function transforms the parsed request for forwarding:
- Converts absolute URLs to relative paths (as required by HTTP/1.1 to origin servers).
- Sets the `Host` header to match the target.
- Strips proxy-specific headers (`Proxy-Connection`, `Proxy-Authorization`, `Proxy-Authenticate`).
- Forces `Connection: close`.

### 4.4 Threading (Requirement D)

**Module:** `proxy_server.py`

Every incoming client connection is handled in a separate daemon thread:

```python
t = threading.Thread(target=handle_client, args=(client_sock, client_addr), daemon=True)
t.start()
```

This allows the proxy to serve multiple clients simultaneously. Using daemon threads means all worker threads terminate automatically when the main thread exits (e.g., on `Ctrl+C`).

Thread safety is ensured across all shared resources:
- **Cache:** Protected by `threading.Lock` inside `CacheManager`.
- **Filter rules:** Protected by `threading.Lock` inside `FilterManager`.
- **Log counters and ring buffer:** Each protected by its own `threading.Lock`.

### 4.5 Logging (Requirement E)

**Module:** `proxy_logger.py`

The logging system has two output channels:

1. **File log** (`logs/proxy.log`): Uses Python's standard `logging` module with a `FileHandler`. Each entry is a human-readable single line containing timestamp, client address, target address, method, URL, status code, response size, and flags for cache hits, blocks, and errors.

2. **In-memory ring buffer**: A `collections.deque(maxlen=500)` stores the last 500 log entries as structured dictionaries. This buffer is consumed by the admin interface for real-time log viewing without reading the file.

Each log entry records:
- Client IP address and port
- Target server address and port
- HTTP method and full URL
- Timestamp (UTC)
- Response status code and size
- Cache hit/miss indicator
- Error messages (if the request failed)
- Whether the request was blocked by the filter

Global counters track `total_requests`, `blocked_requests`, and `active_connections` for the dashboard.

### 4.6 Content Caching (Requirement F)

**Module:** `cache_manager.py`

The `CacheManager` class provides a thread-safe in-memory LRU cache:

- **Storage:** An `OrderedDict` keyed by URL, storing the full response bytes, parsed headers, timestamp, expiry time, and byte size.
- **Cache lookup:** `get(url)` returns cached bytes if the entry exists and has not expired. Expired entries are evicted on access. Accessed entries are moved to the end (most-recently used).
- **Cache insertion:** `put(url, response, headers)` stores a response with a TTL determined by:
  1. `Cache-Control: max-age=N` (highest priority)
  2. `Expires` header (parsed via `email.utils.parsedate_to_datetime`)
  3. A configurable default TTL of 300 seconds (fallback)
- **Cache-Control compliance:** Responses with `Cache-Control: no-store` or `no-cache` are never cached.
- **Eviction:** When the cache exceeds `MAX_CACHE_SIZE` (200 entries), the oldest (least-recently used) entry is evicted.
- **Admin operations:** `invalidate(url)` removes a single entry; `clear()` flushes everything; `get_stats()` and `get_entries()` expose data for the dashboard.

### 4.7 Blacklist / Whitelist (Requirement G)

**Module:** `filter_manager.py`

The `FilterManager` class supports two filtering modes:

- **Blacklist mode** (default): Requests to domains matching any blacklist pattern are rejected with a `403 Forbidden` response. All other requests are allowed.
- **Whitelist mode**: Only requests to domains matching a whitelist pattern are allowed. Everything else is rejected.

Pattern matching supports:
- Exact domain names (e.g., `example.com`)
- Wildcard subdomains (e.g., `*.example.com` matches `www.example.com`, `mail.example.com`, etc.) via Python's `fnmatch` module.

Rules are persisted to `data/blacklist.json` and automatically loaded on startup. The admin interface allows adding, removing, and switching modes at runtime — changes take effect immediately and are saved to disk.

### 4.8 HTTPS Proxy — Bonus (Requirement H)

**Modules:** `ssl_handler.py`, `generate_cert.py`

The HTTPS implementation supports two modes:

**Tunnel Mode (default, `ENABLE_MITM=False`):**
When a client sends a `CONNECT` request, the proxy:
1. Opens a TCP connection to the target server.
2. Sends `HTTP/1.1 200 Connection Established` back to the client.
3. Uses `select.select()` to bidirectionally relay raw bytes between the client and target sockets. The proxy never sees the encrypted content.

**MITM Mode (`ENABLE_MITM=True`):**
For debugging and educational purposes, the proxy can decrypt HTTPS traffic:
1. **Certificate generation** (`generate_cert.py`): On first run, a self-signed CA key-pair is generated (`certs/ca.pem`, `certs/ca.key`). For each unique hostname, a certificate signed by this CA is generated and cached on disk.
2. The proxy sends `200 Connection Established` to the client.
3. The client socket is wrapped in TLS using the dynamically generated certificate (acting as a TLS server).
4. A real TLS connection is opened to the target server (acting as a TLS client).
5. The decrypted HTTP request is read, passed through the filtering and caching pipeline, forwarded to the target, and the response is relayed back.

This requires the client to trust the proxy's CA certificate (`certs/ca.pem`).

### 4.9 Admin Interface — Bonus (Requirement I)

**Modules:** `admin_interface.py`, `templates/admin.html`, `static/style.css`

A Flask-based web application runs on a separate port (8080) in a daemon thread alongside the proxy. It provides:

**Dashboard Tab:**
- Real-time statistics: total requests, active connections, cache hit rate, cached entries count, blocked requests, and total cache size.
- A table of the 20 most recent requests with method badges, URLs, status codes, and cache-hit indicators.
- Auto-refreshes every 5 seconds via `fetch()` calls to the JSON API.

**Logs Tab:**
- Full scrollable log viewer showing up to 500 recent entries.
- Each row shows timestamp, client/target addresses, method, URL, status, response size, cache/block status, and error messages.
- Color-coded rows for blocked (red) and errored (yellow) requests.

**Cache Tab:**
- Lists all currently cached URLs with their size, cache timestamp, and remaining TTL.
- Per-entry "Remove" button for selective invalidation.
- "Clear All Cache" button to flush the entire cache.

**Filters Tab:**
- Displays the current filtering mode (blacklist or whitelist) with a mode switcher.
- Lists all blacklist and whitelist entries with per-entry "Remove" buttons.
- An "Add Rule" form for adding new domain patterns at runtime.

**API Endpoints:**

| Endpoint | Method | Description |
|---|---|---|
| `/api/stats` | GET | Returns JSON with all counters and cache stats |
| `/api/logs` | GET | Returns recent log entries as JSON |
| `/api/cache` | GET | Returns cached entry metadata as JSON |
| `/api/cache/clear` | POST | Clears the entire cache |
| `/api/cache/invalidate` | POST | Removes a single cached URL |
| `/api/filters` | GET | Returns current filter rules as JSON |
| `/api/filters/add` | POST | Adds a blacklist or whitelist pattern |
| `/api/filters/remove` | POST | Removes a pattern |
| `/api/filters/mode` | POST | Switches between blacklist and whitelist mode |

---

## 5. Implementation Details

### Thread Safety

All modules that maintain shared mutable state use `threading.Lock`:

- `CacheManager`: A single lock protects the `OrderedDict` and hit/miss counters.
- `FilterManager`: A single lock protects the rules dictionary and file writes.
- `proxy_logger`: Separate locks for the ring buffer and the counters dictionary, minimizing contention.

### Error Handling

The proxy uses layered error handling:
- **Parse failures** return `400 Bad Request` to the client.
- **Target unreachable** returns `502 Bad Gateway`.
- **Socket timeouts** are caught and logged without crashing the server.
- All socket operations in `handle_client` are wrapped in `try/except/finally`, ensuring the client socket is always closed and the active-connection counter is decremented.

### Configuration

All tuneable parameters reside in `config.py`:

| Parameter | Default | Description |
|---|---|---|
| `PROXY_PORT` | 8888 | Port the proxy listens on |
| `ADMIN_PORT` | 8080 | Port for the admin web UI |
| `CACHE_DEFAULT_TTL` | 300s | Default cache entry lifetime |
| `MAX_CACHE_SIZE` | 200 | Maximum number of cached responses |
| `BUFFER_SIZE` | 8192 | Socket read buffer size |
| `SOCKET_TIMEOUT` | 30s | Socket operation timeout |
| `ENABLE_MITM` | False | Enable HTTPS man-in-the-middle mode |

### Dependencies

| Package | Purpose |
|---|---|
| `flask` | Web framework for the admin interface |
| `pyOpenSSL` | Certificate generation for MITM mode |

Both are listed in `requirements.txt` and installed via `pip install -r requirements.txt`.

---

## 6. Testing

### Test Environment

- **Operating System:** Windows 10/11
- **Python Version:** 3.11
- **Test Tool:** `curl.exe` (built-in on Windows), web browser

### Test Cases and Results

#### Test 1: Basic HTTP Proxy

**Command:**
```
curl.exe -x http://localhost:8888 http://httpbin.org/get
```

**Result:** The proxy successfully forwarded the request and returned the full JSON response from httpbin.org, including correct headers. The log file recorded the request with status 200.

#### Test 2: Caching — Cache Miss then Cache Hit

**Commands:**
```
curl.exe -x http://localhost:8888 http://httpbin.org/get   (1st request — cache miss)
curl.exe -x http://localhost:8888 http://httpbin.org/get   (2nd request — cache hit)
```

**Result:** The first request was forwarded to httpbin.org (cache miss). The second request was served from cache (cache hit). The admin dashboard confirmed:
- `total_requests: 2`
- `hits: 1`, `misses: 1`
- `hit_rate: 50.0%`
- `entry_count: 1`

The log file showed `CACHE-HIT` on the second request.

#### Test 3: HTTPS Tunneling

**Command:**
```
curl.exe -x http://localhost:8888 https://httpbin.org/get
```

**Result:** The proxy handled the `CONNECT` request, established a tunnel, and the encrypted HTTPS request reached httpbin.org successfully. The response was returned to the client with the correct HTTPS origin URL.

#### Test 4: Blacklist Filtering

**Steps:**
1. Added `example.com` to the blacklist via the admin interface.
2. Sent a request through the proxy:
   ```
   curl.exe -x http://localhost:8888 http://example.com
   ```

**Result:** The proxy returned `403 Forbidden` with the message "This domain has been blocked by the proxy." The admin stats showed `blocked_requests: 1`.

#### Test 5: Admin Interface

**Action:** Opened `http://localhost:8080` in a web browser.

**Result:** The dashboard loaded with real-time stats cards showing total requests, active connections, cache hit rate, cached entries, blocked requests, and cache size. The recent activity table populated with request logs. All four tabs (Dashboard, Logs, Cache, Filters) functioned correctly.

#### Test 6: Cache Management via Admin

**Steps:**
1. Verified cached entries appeared in the Cache tab.
2. Clicked "Remove" on a specific entry — it was invalidated.
3. Clicked "Clear All Cache" — all entries were removed.
4. Confirmed via `/api/cache` that the cache was empty.

**Result:** All cache management operations worked correctly.

#### Test 7: Filter Management via Admin

**Steps:**
1. Added `*.bad-site.com` to the blacklist via the Filters tab.
2. Verified it appeared in the filter rules list.
3. Removed it using the "Remove" button.
4. Confirmed `data/blacklist.json` reflected the changes on disk.

**Result:** Filter rules were added, displayed, removed, and persisted correctly.

#### Test 8: Concurrent Connections

**Approach:** Opened multiple simultaneous curl requests to the proxy targeting different URLs.

**Result:** All requests were handled concurrently without errors. The active connections counter in the dashboard reflected the concurrent load.

### Summary of Test Results

| Test | Feature | Status |
|---|---|---|
| HTTP Proxy Forwarding | Requirement A | Passed |
| HTTPS Tunneling | Requirement A/H | Passed |
| Socket Programming | Requirement B | Passed |
| Request Parsing | Requirement C | Passed |
| Multi-threading | Requirement D | Passed |
| Logging (file + in-memory) | Requirement E | Passed |
| Content Caching (TTL, LRU) | Requirement F | Passed |
| Blacklist/Whitelist | Requirement G | Passed |
| HTTPS MITM (Bonus) | Requirement H | Implemented |
| Admin Interface (Bonus) | Requirement I | Passed |

---

## 7. Challenges Faced

### 1. Port Conflicts on Windows

During development, we discovered that Windows does not always release TCP ports immediately after a process is killed. Multiple zombie processes could bind to the same port, causing the admin interface to serve stale data from old processes. **Solution:** We ensured all old processes were terminated before restarting.

### 2. Thread-Safe Shared State

The proxy's multi-threaded nature required careful synchronization. The cache, filter rules, and log buffers are all accessed from multiple threads. Using coarse-grained `threading.Lock` per module kept the implementation simple while preventing race conditions.

### 3. HTTP Request Buffering

Determining when a complete HTTP request has been received is non-trivial. We read until `\r\n\r\n` (end of headers), then check for `Content-Length` to read the body. For simplicity, we do not handle chunked transfer encoding in client requests (which is rare for client-to-proxy communication).

### 4. HTTPS CONNECT Semantics

The CONNECT method requires a fundamentally different flow: the proxy must send `200 Connection Established` before any data relay begins, and then transparently forward raw bytes. We used `select.select()` for efficient bidirectional relay.

### 5. Forcing Connection: close

HTTP/1.1 defaults to keep-alive connections, which would require the proxy to track Content-Length or chunked encoding to know when one response ends and the next begins. By forcing `Connection: close`, we ensure the target server closes the connection after each response, making it straightforward to read the complete response.

---

## 8. Conclusion

We successfully implemented a fully-featured caching proxy server that meets all the required specifications and both bonus features. The proxy handles HTTP and HTTPS traffic, caches responses intelligently based on standard HTTP headers, filters requests by domain, logs all activity, and provides a web-based administration interface for real-time monitoring and management.

The modular architecture makes the codebase maintainable and extensible. Each feature is isolated in its own module, and the central configuration file makes it easy to adjust parameters without modifying code. The thread-per-connection model provides adequate concurrency for educational and debugging scenarios.

Both bonus features add significant value: the HTTPS MITM capability enables inspection of encrypted traffic for educational purposes, and the admin dashboard provides an intuitive interface for managing the proxy without command-line tools.

---

*End of Report*
