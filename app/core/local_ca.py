"""
Certificates for the phone app (HTTPS on the local network, needed for "Install app" + Share on Android).

* a small certificate authority made on this PC, **name-constrained to private / local addresses**
  (10.x, 172.16-31.x, 192.168.x, 169.254.x, 127.x, localhost) — even if it leaked it could not vouch
  for any internet site
* its private key is stored encrypted with Windows DPAPI (only your Windows account can use it)
* the server certificate lists this PC's current local IPs and is re-made when they change
* the phone installs the CA once (Settings › Security › Install a certificate › CA certificate) and
  can remove it there at any time
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import DATA_DIR

CA_DIR = DATA_DIR / "phone_ca"
PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8"]
_ENTROPY = b"StudyHub phone CA"


def _protect(data: bytes) -> bytes:
    from app.core.vault import protect
    return protect(data, _ENTROPY)


def _unprotect(data: bytes) -> bytes:
    from app.core.vault import unprotect
    return unprotect(data, _ENTROPY)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    CA_DIR.mkdir(parents=True, exist_ok=True)
    cert_file, key_file = CA_DIR / "ca.crt", CA_DIR / "ca.key.dpapi"
    if cert_file.exists() and key_file.exists():
        try:
            cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
            key = serialization.load_pem_private_key(_unprotect(key_file.read_bytes()), None)
            if cert.not_valid_after_utc > _now() + dt.timedelta(days=30):
                return cert, key
        except Exception:
            pass  # damaged / from another Windows account: make a new one
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Study Hub local ({socket.gethostname()})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Study Hub")])
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS] + [x509.DNSName("localhost")],
        excluded_subtrees=None)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(constraints, critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(_protect(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption())))
    return cert, key


def ca_der() -> bytes:
    """What the phone downloads and installs."""
    cert, _ = ensure_ca()
    return cert.public_bytes(serialization.Encoding.DER)


def ca_fingerprint() -> str:
    cert, _ = ensure_ca()
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()[:47]


def server_context(ips: list[str]):
    """ssl.SSLContext for the phone server, valid for the given local IPs (re-made when they change)."""
    import ssl
    ca, ca_key = ensure_ca()
    ips = sorted({*ips, "127.0.0.1"})
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips if ipaddress.ip_address(ip).is_private]
    san.append(x509.DNSName("localhost"))
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Study Hub")]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # ssl only loads from files: write them for a moment, load into memory, delete
    fd, path = tempfile.mkstemp(suffix=".pem", dir=str(CA_DIR))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            f.write(ca.public_bytes(serialization.Encoding.PEM))
        ctx.load_cert_chain(path)
    finally:
        Path(path).unlink(missing_ok=True)
    return ctx
