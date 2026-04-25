"""
HTTP request parsing and header manipulation for the proxy server.

Handles:
  - Parsing raw HTTP request bytes into a structured dict.
  - Rebuilding the request for forwarding (absolute -> relative URL,
    stripping proxy-specific headers, setting Host).
  - Extracting host/port from CONNECT requests.

Contributed by: Ourouba
"""

from urllib.parse import urlparse


def parse_request(raw_data: bytes) -> dict | None:
    """
    Parse raw HTTP request bytes into a structured dictionary.

    Returns dict with keys:
        method, url, host, port, path, headers (dict), body (bytes),
        version (e.g. 'HTTP/1.1'), raw_first_line
    Returns None if the data cannot be parsed.
    """
    try:
        if b"\r\n" not in raw_data:
            return None

        header_end = raw_data.find(b"\r\n\r\n")
        if header_end == -1:
            header_section = raw_data
            body = b""
        else:
            header_section = raw_data[:header_end]
            body = raw_data[header_end + 4:]

        lines = header_section.decode("utf-8", errors="replace").split("\r\n")
        first_line = lines[0]
        parts = first_line.split()
        if len(parts) < 3:
            return None

        method = parts[0].upper()
        url = parts[1]
        version = parts[2]

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()

        host, port, path = _extract_target(method, url, headers)

        return {
            "method": method,
            "url": url,
            "host": host,
            "port": port,
            "path": path,
            "headers": headers,
            "body": body,
            "version": version,
            "raw_first_line": first_line,
        }
    except Exception:
        return None


def _extract_target(method: str, url: str, headers: dict) -> tuple:
    """Derive (host, port, path) from the request line and headers."""
    if method == "CONNECT":
        # CONNECT host:port HTTP/1.1
        host_port = url
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443
        return host, port, ""

    parsed = urlparse(url)
    if parsed.hostname:
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
    else:
        # Relative URL — rely on Host header
        path = url
        host_header = headers.get("Host", "")
        if ":" in host_header:
            host, port_str = host_header.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_header
            port = 80

    return host, port, path


def rebuild_request(parsed: dict) -> bytes:
    """
    Rebuild the HTTP request for forwarding to the target server.

    Converts absolute URLs to relative paths, sets the Host header,
    strips proxy-specific headers, and forces Connection: close.
    """
    path = parsed["path"] or "/"
    first_line = f"{parsed['method']} {path} {parsed['version']}\r\n"

    headers = dict(parsed["headers"])

    # Ensure Host is present
    host_value = parsed["host"]
    if parsed["port"] not in (80, 443):
        host_value = f"{parsed['host']}:{parsed['port']}"
    headers["Host"] = host_value

    # Strip proxy-specific headers
    for hdr in ("Proxy-Connection", "Proxy-Authorization", "Proxy-Authenticate"):
        headers.pop(hdr, None)

    # Force connection close for simplicity
    headers["Connection"] = "close"

    header_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    request_bytes = (first_line + header_lines + "\r\n").encode("utf-8")

    if parsed["body"]:
        request_bytes += parsed["body"]

    return request_bytes
