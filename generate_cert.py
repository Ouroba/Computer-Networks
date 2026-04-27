"""
Self-signed CA and per-host certificate generation for MITM HTTPS interception.

On first invocation, creates a CA key-pair in certs/ca.pem + certs/ca.key.
Subsequent calls to generate_host_cert() produce hostname-specific certificates
signed by that CA. Generated host certs are cached on disk so they are reused
across proxy restarts.

Contributed by: Shahd
"""

import os
import time

from OpenSSL import crypto

import config

os.makedirs(config.CERTS_DIR, exist_ok=True)


def _ensure_ca() -> tuple[crypto.X509, crypto.PKey]:
    """Return (ca_cert, ca_key), creating them if they do not exist yet."""
    if os.path.isfile(config.CA_CERT) and os.path.isfile(config.CA_KEY):
        with open(config.CA_CERT, "rb") as f:
            ca_cert = crypto.load_certificate(crypto.FILETYPE_PEM, f.read())
        with open(config.CA_KEY, "rb") as f:
            ca_key = crypto.load_privatekey(crypto.FILETYPE_PEM, f.read())
        return ca_cert, ca_key

    ca_key = crypto.PKey()
    ca_key.generate_key(crypto.TYPE_RSA, 2048)

    ca_cert = crypto.X509()
    subj = ca_cert.get_subject()
    subj.C = "US"
    subj.ST = "Proxy"
    subj.O = "Proxy CA"
    subj.CN = "Proxy CA Root"
    ca_cert.set_serial_number(int(time.time()))
    ca_cert.gmtime_adj_notBefore(0)
    ca_cert.gmtime_adj_notAfter(10 * 365 * 24 * 3600)  # 10 years
    ca_cert.set_issuer(subj)
    ca_cert.set_pubkey(ca_key)
    ca_cert.add_extensions([
        crypto.X509Extension(b"basicConstraints", True, b"CA:TRUE"),
        crypto.X509Extension(b"keyUsage", True, b"keyCertSign, cRLSign"),
        crypto.X509Extension(
            b"subjectKeyIdentifier", False, b"hash", subject=ca_cert
        ),
    ])
    ca_cert.sign(ca_key, "sha256")

    with open(config.CA_CERT, "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, ca_cert))
    with open(config.CA_KEY, "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, ca_key))

    return ca_cert, ca_key


def generate_host_cert(hostname: str) -> tuple[str, str]:
    """
    Return (cert_path, key_path) for *hostname*, generating them if needed.

    The cert is signed by the proxy CA so browsers that trust ca.pem
    will accept the connection.
    """
    cert_path = os.path.join(config.CERTS_DIR, f"{hostname}.pem")
    key_path = os.path.join(config.CERTS_DIR, f"{hostname}.key")

    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    ca_cert, ca_key = _ensure_ca()

    host_key = crypto.PKey()
    host_key.generate_key(crypto.TYPE_RSA, 2048)

    host_cert = crypto.X509()
    subj = host_cert.get_subject()
    subj.CN = hostname
    host_cert.set_serial_number(int(time.time() * 1000))
    host_cert.gmtime_adj_notBefore(0)
    host_cert.gmtime_adj_notAfter(365 * 24 * 3600)
    host_cert.set_issuer(ca_cert.get_subject())
    host_cert.set_pubkey(host_key)
    host_cert.add_extensions([
        crypto.X509Extension(
            b"subjectAltName", False, f"DNS:{hostname}".encode()
        ),
    ])
    host_cert.sign(ca_key, "sha256")

    with open(cert_path, "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, host_cert))
    with open(key_path, "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, host_key))

    return cert_path, key_path
