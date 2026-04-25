"""
Main entry point for the caching proxy server.

Responsibilities:
  - Bind a TCP socket and listen for incoming client connections.
  - Spawn a thread per connection for concurrent handling.
  - Dispatch HTTP requests through the filter, cache, and forwarding pipeline.
  - Delegate HTTPS CONNECT requests to ssl_handler.
  - Start the admin web interface in a background daemon thread.

Contributed by: Ourouba
"""

import socket
import threading
import sys

import config
import request_parser
import proxy_logger
from cache_manager import cache
from filter_manager import filters

# Lazy-imported at runtime so the module can be loaded even if pyOpenSSL
# is not installed (ssl_handler will gracefully degrade).
ssl_handler = None


def _import_ssl_handler():
    global ssl_handler
    try:
        import ssl_handler as _sh
        ssl_handler = _sh
    except ImportError:
        ssl_handler = None


#  Response helpers

_BLOCKED_RESPONSE = (
    b"HTTP/1.1 403 Forbidden\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\n\r\n"
    b"403 Forbidden.\r\n"
    b"This domain has been blocked by the proxy."
)

_BAD_REQUEST = (
    b"HTTP/1.1 400 Bad Request\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\n\r\n"
    b"400 Bad Request"
)

_BAD_GATEWAY = (
    b"HTTP/1.1 502 Bad Gateway\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\n\r\n"
    b"502 Bad Gateway\r\n"
    b"Could not reach the target server."
)


#  Per-client handler 

def handle_client(client_sock: socket.socket, client_addr: tuple):
    """Handle a single client connection (runs in its own thread)."""
    proxy_logger.connection_opened()
    client_ip, client_port = client_addr

    try:
        client_sock.settimeout(config.SOCKET_TIMEOUT)
        raw_data = _recv_request(client_sock)
        if not raw_data:
            return

        parsed = request_parser.parse_request(raw_data)
        if parsed is None:
            client_sock.sendall(_BAD_REQUEST)
            proxy_logger.log_request(
                client_ip=client_ip, client_port=client_port,
                method="?", url="?", status_code=400,
                error_message="Unparseable request",
            )
            return

        method = parsed["method"]
        host = parsed["host"]
        port = parsed["port"]
        url = parsed["url"]

        #  Blacklist / whitelist check 
        if not filters.is_allowed(host, url):
            client_sock.sendall(_BLOCKED_RESPONSE)
            proxy_logger.log_request(
                client_ip=client_ip, client_port=client_port,
                target_host=host, target_port=port,
                method=method, url=url, status_code=403, blocked=True,
            )
            return

        #  HTTPS CONNECT 
        if method == "CONNECT":
            _handle_connect(client_sock, client_addr, parsed)
            return

        #  Cache lookup (HTTP only)
        if method == "GET":
            cached = cache.get(url)
            if cached is not None:
                client_sock.sendall(cached)
                proxy_logger.log_request(
                    client_ip=client_ip, client_port=client_port,
                    target_host=host, target_port=port,
                    method=method, url=url,
                    status_code=_extract_status(cached),
                    response_size=len(cached),
                    cache_hit=True,
                )
                return

        #  Forward to target server 
        forward_data = request_parser.rebuild_request(parsed)
        response = _forward_request(host, port, forward_data)

        if response is None:
            client_sock.sendall(_BAD_GATEWAY)
            proxy_logger.log_request(
                client_ip=client_ip, client_port=client_port,
                target_host=host, target_port=port,
                method=method, url=url, status_code=502,
                error_message="Target server unreachable",
            )
            return

        # Cache storage (GET responses only) 
        status_code = _extract_status(response)
        if method == "GET" and 200 <= status_code < 400:
            resp_headers = _parse_response_headers(response)
            cache.put(url, response, resp_headers)

        client_sock.sendall(response)
        proxy_logger.log_request(
            client_ip=client_ip, client_port=client_port,
            target_host=host, target_port=port,
            method=method, url=url,
            status_code=status_code,
            response_size=len(response),
        )

    except socket.timeout:
        proxy_logger.log_request(
            client_ip=client_ip, client_port=client_port,
            error_message="Client socket timed out",
        )
    except Exception as exc:
        proxy_logger.log_request(
            client_ip=client_ip, client_port=client_port,
            error_message=str(exc),
        )
    finally:
        try:
            client_sock.close()
        except OSError:
            pass
        proxy_logger.connection_closed()


# HTTPS CONNECT delegation 

def _handle_connect(client_sock, client_addr, parsed):
    """Delegate a CONNECT tunnel to ssl_handler (tunnel or MITM)."""
    _import_ssl_handler()
    if ssl_handler is None:
        client_sock.sendall(_BAD_GATEWAY)
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=parsed["host"], target_port=parsed["port"],
            method="CONNECT", url=parsed["url"], status_code=502,
            error_message="ssl_handler not available",
        )
        return

    ssl_handler.handle_connect(
        client_sock, client_addr,
        parsed["host"], parsed["port"],
    )


#  Low-level networking helpers

def _recv_request(sock: socket.socket) -> bytes:
    """
    Receive the full HTTP request from the client.

    Reads headers first, then the body according to Content-Length (if present).
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(config.BUFFER_SIZE)
        if not chunk:
            return data
        data += chunk

    header_end = data.index(b"\r\n\r\n") + 4
    headers_raw = data[:header_end].decode("utf-8", errors="replace")

    content_length = 0
    for line in headers_raw.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            break

    body_received = len(data) - header_end
    while body_received < content_length:
        chunk = sock.recv(config.BUFFER_SIZE)
        if not chunk:
            break
        data += chunk
        body_received += len(chunk)

    return data


def _forward_request(host: str, port: int, data: bytes) -> bytes | None:
    """Open a TCP connection to the target and relay data, returning the full response."""
    try:
        target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target_sock.settimeout(config.SOCKET_TIMEOUT)
        target_sock.connect((host, port))
        target_sock.sendall(data)

        response = b""
        while True:
            chunk = target_sock.recv(config.BUFFER_SIZE)
            if not chunk:
                break
            response += chunk
        target_sock.close()
        return response if response else None
    except Exception:
        return None


def _extract_status(response: bytes) -> int:
    """Pull the numeric status code from the first line of an HTTP response."""
    try:
        first_line = response.split(b"\r\n", 1)[0].decode()
        return int(first_line.split()[1])
    except Exception:
        return 0


def _parse_response_headers(response: bytes) -> dict:
    """Parse response headers into a dict (first occurrence wins)."""
    headers = {}
    try:
        header_block = response.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
        for line in header_block.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers.setdefault(k.strip(), v.strip())
    except Exception:
        pass
    return headers


#  Server lifecycle 

def start_proxy():
    """Bind, listen, and accept connections in an infinite loop."""
    _import_ssl_handler()

    # Start admin interface in a daemon thread
    try:
        from admin_interface import start_admin
        admin_thread = threading.Thread(target=start_admin, daemon=True)
        admin_thread.start()
        print(f"[*] Admin interface running on http://{config.ADMIN_HOST}:{config.ADMIN_PORT}")
    except Exception as exc:
        print(f"[!] Could not start admin interface: {exc}")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((config.PROXY_HOST, config.PROXY_PORT))
    server_sock.listen(50)
    print(f"[*] Proxy server listening on {config.PROXY_HOST}:{config.PROXY_PORT}")

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        print("\n[*] Shutting down proxy server...")
    finally:
        server_sock.close()


if __name__ == "__main__":
    start_proxy()