"""
Central configuration for the caching proxy server.
All tuneable constants live here so every other module imports from one place.

Contributed by: All
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PROXY_HOST = "0.0.0.0"
PROXY_PORT = 8888

ADMIN_HOST = "0.0.0.0"
ADMIN_PORT = 8080

CACHE_DEFAULT_TTL = 300          # seconds
MAX_CACHE_SIZE = 200             # max number of cached entries

BUFFER_SIZE = 8192               # bytes per recv() call
SOCKET_TIMEOUT = 30              # seconds before a socket operation times out


LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "proxy.log")
LOG_RING_SIZE = 500              # in-memory ring buffer capacity for admin UI


BLACKLIST_FILE = os.path.join(BASE_DIR, "data", "blacklist.json")

#  SSL / MITM 
CERTS_DIR = os.path.join(BASE_DIR, "certs")
ENABLE_MITM = False             # flip to True to decrypt HTTPS via MITM
CA_CERT = os.path.join(CERTS_DIR, "ca.pem")
CA_KEY = os.path.join(CERTS_DIR, "ca.key")
