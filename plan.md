**Module Breakdown**
**A. config.py — Central Configuration**
PROXY_HOST, PROXY_PORT (default 0.0.0.0:8888)
ADMIN_PORT (default 8080)
CACHE_DEFAULT_TTL (default 300 seconds)
MAX_CACHE_SIZE (number of entries)
BUFFER_SIZE (default 8192 bytes)
LOG_FILE path
BLACKLIST_FILE path
CERTS_DIR path
ENABLE_MITM toggle (bool)

**Ourouba Al Sahmarany - Team Memebr 1**

B. proxy_server.py — Main Server (Team Member 1)

Create a TCP socket, bind to PROXY_HOST:PROXY_PORT, listen for connections.

On each accept(), spawn a new threading.Thread targeting a handle_client function.

handle_client(client_socket, client_addr):

Receive raw request data from client.

Call request_parser.parse_request() to extract method, URL, host, port, headers.

Check filter_manager.is_allowed(host, url) — if blocked, send 403 response and log.

If method is CONNECT (HTTPS): delegate to ssl_handler.handle_connect().

Else (HTTP): check cache_manager.get(url) — if hit, return cached response.

If cache miss: open socket to target server, forward request, receive response.

Parse response headers for caching directives, store in cache if cacheable.

Relay response back to client.

Log everything via proxy_logger.

Graceful shutdown on Ctrl+C (close listening socket, join threads).

C. request_parser.py — Request Parsing (Team Member 1)

parse_request(raw_data: bytes) -> dict: Returns { method, url, host, port, path, headers, body, version }.

Handle absolute URLs (GET http://example.com/path
) and relative URLs.

rebuild_request(parsed: dict) -> bytes: Rebuild the request for forwarding — set Host header, strip Proxy-Connection, convert absolute URL to relative path.

Handle CONNECT host:port for HTTPS tunneling.

F. proxy_logger.py — Logging (Team Member 1)

Use Python's logging module with both file handler (logs/proxy.log) and an in-memory ring buffer (last 500 entries for admin UI).

Each log entry: { timestamp, client_ip, client_port, target_host, target_port, method, url, status_code, response_size, error_message, cache_hit }.

log_request(...): Called after each request is handled.

get_recent_logs(n=100) -> list[dict]: Return recent entries for admin UI.

I. admin_interface.py — Web Admin UI (Team Member 1)

Flask app running on ADMIN_PORT in a separate daemon thread.

Routes:

GET / — Dashboard: active connections count, cache stats, recent logs.

GET /logs — Full log viewer with filtering.

GET /cache — List cached entries with option to invalidate.

POST /cache/clear — Clear entire cache.

GET /filters — View blacklist/whitelist rules.

POST /filters/add — Add a rule.

POST /filters/remove — Remove a rule.

GET /stats — JSON API for real-time stats (cache hit rate, active connections, total requests).

Single HTML template (templates/admin.html) with embedded JS for dynamic updates.

Clean, modern UI using minimal CSS (no heavy frameworks needed).

J. templates/admin.html — Admin Dashboard Template (Team Member 1)

Tabbed interface: Dashboard / Logs / Cache / Filters.

Dashboard tab: cards showing total requests, cache hit rate, active connections, blocked requests.

Logs tab: scrollable table with timestamp, client, target, method, URL, status, cache hit.

Cache tab: table of cached URLs with TTL remaining and invalidate buttons.

Filters tab: current rules display, form to add/remove rules, mode toggle.

Auto-refresh every 5 seconds via fetch to /stats.

**Josephina Sakr -  Team Member 2**

D. cache_manager.py — Content Caching (Team Member 2)

Thread-safe in-memory dict: { url: { response_bytes, headers, timestamp, ttl } }.

Use threading.Lock for concurrent access.

get(url) -> bytes | None: Return cached response if valid (not expired).

put(url, response_bytes, response_headers): Store response; extract TTL from Cache-Control: max-age=X, Expires header, or fall back to CACHE_DEFAULT_TTL. Skip caching for Cache-Control: no-store or no-cache.

invalidate(url): Remove a specific entry.

clear(): Flush entire cache.

get_stats() -> dict: Return hit count, miss count, entry count, total size.

get_entries() -> list: Return list of cached URLs with metadata (for admin UI).

Automatic eviction: if cache exceeds MAX_CACHE_SIZE, evict oldest entry (LRU-style).

E. filter_manager.py — Blacklist/Whitelist (Team Member 2)

Load rules from data/blacklist.json on startup: { "blacklist": ["bad.com", ...], "whitelist": ["good.com", ...], "mode": "blacklist" }.

is_allowed(host, url) -> bool: In blacklist mode, block if host/URL matches any blacklist pattern (supports wildcard *.example.com). In whitelist mode, block if NOT in whitelist.

add_rule(rule_type, pattern) / remove_rule(rule_type, pattern): Modify lists at runtime.

save(): Persist current rules to JSON file.

get_rules() -> dict: Return current rules (for admin UI).

**Shahd Moughrabi - Team Member 3**

G. ssl_handler.py — HTTPS Handling (Team Member 3)

Tunnel mode (non-MITM): On CONNECT, send 200 Connection Established to client, then blindly relay bytes between client and target using select or threading (two relay threads: client->server, server->client).

MITM mode (when ENABLE_MITM=True):

Dynamically generate a certificate for the requested hostname signed by our CA (via generate_cert.py / pyOpenSSL).

Send 200 Connection Established to client.

Wrap client socket with SSL using the generated cert.

Open SSL connection to real target server.

Read decrypted HTTP request from client, apply caching/filtering, forward to target, relay response.

Certs are cached on disk in certs/ to avoid regenerating.

H. generate_cert.py — Certificate Generation (Team Member 3)

On first run, generate a CA key + self-signed CA certificate and save to certs/ca.pem, certs/ca.key.

generate_host_cert(hostname) -> (cert_path, key_path): Generate a cert for the given hostname signed by the CA. Cache in certs/{hostname}.pem.

Uses pyOpenSSL (or cryptography library).