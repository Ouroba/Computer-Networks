"""
HTTPS CONNECT handler for the proxy server.

Two operating modes:
  1. **Tunnel mode** (default): blindly relay encrypted bytes between the
     client and the target server — zero decryption, full privacy.
  2. **MITM mode** (config.ENABLE_MITM = True): dynamically generate a
     per-host certificate signed by the proxy CA, decrypt traffic on both
     sides, and pass clear-text HTTP through the cache / filter pipeline.

Contributed by: Shahd
"""

import select
import socket
import ssl
import threading

import config
import proxy_logger
import request_parser
from cache_manager import cache
from filter_manager import filters

_CONNECT_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def handle_connect(client_sock: socket.socket, client_addr: tuple,
                   host: str, port: int):
    """
    Called by proxy_server when it receives an HTTP CONNECT request.

    Decides between tunnel and MITM based on config.ENABLE_MITM.
    """
    if config.ENABLE_MITM:
        _handle_mitm(client_sock, client_addr, host, port)
    else:
        _handle_tunnel(client_sock, client_addr, host, port)


def _handle_tunnel(client_sock: socket.socket, client_addr: tuple,
                   host: str, port: int):
    """Establish a blind TCP tunnel between client and target."""
    try:
        target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target_sock.settimeout(config.SOCKET_TIMEOUT)
        target_sock.connect((host, port))
    except Exception as exc:
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method="CONNECT", url=f"{host}:{port}",
            status_code=502, error_message=str(exc),
        )
        client_sock.sendall(
            b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
        )
        client_sock.close()
        return

    client_sock.sendall(_CONNECT_OK)
    proxy_logger.log_request(
        client_ip=client_addr[0], client_port=client_addr[1],
        target_host=host, target_port=port,
        method="CONNECT", url=f"{host}:{port}", status_code=200,
    )

    _relay(client_sock, target_sock)


def _relay(sock_a: socket.socket, sock_b: socket.socket):
    """
    Bidirectionally relay bytes between two sockets until one side closes.

    Uses select() for efficient I/O multiplexing.
    """
    sockets = [sock_a, sock_b]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 30)
            if errored:
                break
            for s in readable:
                data = s.recv(config.BUFFER_SIZE)
                if not data:
                    return
                target = sock_b if s is sock_a else sock_a
                target.sendall(data)
    except Exception:
        pass
    finally:
        for s in sockets:
            try:
                s.close()
            except OSError:
                pass


def _handle_mitm(client_sock: socket.socket, client_addr: tuple,
                 host: str, port: int):
    """
    Perform a man-in-the-middle TLS interception.

    Steps:
      1. Generate / load a cert for *host* signed by our CA.
      2. Tell the client "200 Connection Established".
      3. Wrap the client socket in TLS using the generated cert.
      4. Open a real TLS connection to the target server.
      5. Read the plain-text HTTP request, apply filtering / caching,
         forward to target, relay the response.
    """
    try:
        from generate_cert import generate_host_cert
    except ImportError:
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method="CONNECT", url=f"{host}:{port}", status_code=502,
            error_message="pyOpenSSL not installed — cannot MITM",
        )
        client_sock.sendall(
            b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
        )
        client_sock.close()
        return

    cert_path, key_path = generate_host_cert(host)

    client_sock.sendall(_CONNECT_OK)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    try:
        client_tls = server_ctx.wrap_socket(client_sock, server_side=True)
    except ssl.SSLError as exc:
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method="CONNECT", url=f"{host}:{port}", status_code=502,
            error_message=f"Client TLS handshake failed: {exc}",
        )
        client_sock.close()
        return

    target_ctx = ssl.create_default_context()
    raw_target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_target.settimeout(config.SOCKET_TIMEOUT)
    try:
        raw_target.connect((host, port))
        target_tls = target_ctx.wrap_socket(raw_target, server_hostname=host)
    except Exception as exc:
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method="CONNECT", url=f"{host}:{port}", status_code=502,
            error_message=f"Target TLS connection failed: {exc}",
        )
        client_tls.close()
        raw_target.close()
        return

    try:
        _mitm_relay(client_tls, target_tls, client_addr, host, port)
    finally:
        for s in (client_tls, target_tls):
            try:
                s.close()
            except OSError:
                pass


def _mitm_relay(client_tls, target_tls, client_addr, host, port):
    """Read one plain-text HTTP request over the decrypted tunnel and relay."""
    try:
        raw_data = b""
        while b"\r\n\r\n" not in raw_data:
            chunk = client_tls.recv(config.BUFFER_SIZE)
            if not chunk:
                return
            raw_data += chunk

        parsed = request_parser.parse_request(raw_data)
        if parsed is None:
            return

        parsed["host"] = host
        parsed["port"] = port
        url = f"https://{host}{parsed['path']}"

        if not filters.is_allowed(host, url):
            client_tls.sendall(
                b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"
                b"<h1>Blocked by proxy</h1>"
            )
            proxy_logger.log_request(
                client_ip=client_addr[0], client_port=client_addr[1],
                target_host=host, target_port=port,
                method=parsed["method"], url=url, status_code=403, blocked=True,
            )
            return

        if parsed["method"] == "GET":
            cached = cache.get(url)
            if cached is not None:
                client_tls.sendall(cached)
                proxy_logger.log_request(
                    client_ip=client_addr[0], client_port=client_addr[1],
                    target_host=host, target_port=port,
                    method=parsed["method"], url=url,
                    status_code=200, response_size=len(cached), cache_hit=True,
                )
                return

        forward_data = request_parser.rebuild_request(parsed)
        target_tls.sendall(forward_data)

        response = b""
        while True:
            chunk = target_tls.recv(config.BUFFER_SIZE)
            if not chunk:
                break
            response += chunk

        if parsed["method"] == "GET" and response:
            resp_hdrs = _quick_parse_headers(response)
            cache.put(url, response, resp_hdrs)

        client_tls.sendall(response)
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method=parsed["method"], url=url,
            status_code=_status_from(response), response_size=len(response),
        )
    except Exception as exc:
        proxy_logger.log_request(
            client_ip=client_addr[0], client_port=client_addr[1],
            target_host=host, target_port=port,
            method="CONNECT", url=f"https://{host}",
            error_message=f"MITM relay error: {exc}",
        )


def _quick_parse_headers(response: bytes) -> dict:
    headers = {}
    try:
        hdr_block = response.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
        for line in hdr_block.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers.setdefault(k.strip(), v.strip())
    except Exception:
        pass
    return headers


def _status_from(response: bytes) -> int:
    try:
        return int(response.split(b"\r\n", 1)[0].decode().split()[1])
    except Exception:
        return 0
