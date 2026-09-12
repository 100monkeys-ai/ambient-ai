"""Signed, time-limited magic links: possession of the number is proven by opening one.

Token = base64url(issued_at || phone XOR keystream) + "." + hex(HMAC-SHA256(APP_SECRET, that)).
The number is masked with a keystream derived from APP_SECRET and issued_at, so the token
that lands in a URL, an access log, or a browser history does not carry the number in the
clear. The MAC covers the masked payload; verification is constant-time and rejects
tampering and anything older than the TTL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from ambient_ai.settings import MAGIC_LINK_TTL_SECONDS, portal_base_url, require


def _secret() -> bytes:
    return require("APP_SECRET").encode()


def _mac(payload: bytes) -> str:
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def _mask(issued_at: int, phone: bytes) -> bytes:
    stream = hmac.new(_secret(), f"mask|{issued_at}".encode(), hashlib.sha256).digest()
    return bytes(b ^ stream[i % len(stream)] for i, b in enumerate(phone))


def sign(phone: str, now: float | None = None) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = issued_at.to_bytes(8, "big") + _mask(issued_at, phone.encode())
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _mac(payload)


def verify(token: str, now: float | None = None) -> str | None:
    """Return the phone number the token was issued for, or None if invalid or expired."""
    try:
        encoded, mac = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        issued = int.from_bytes(payload[:8], "big")
    except ValueError:
        return None
    if not hmac.compare_digest(mac, _mac(payload)):
        return None
    current = now if now is not None else time.time()
    if current - issued > MAGIC_LINK_TTL_SECONDS or issued - current > 60:
        return None
    try:
        return _mask(issued, payload[8:]).decode()
    except UnicodeDecodeError:
        return None


def portal_link(phone: str) -> str:
    return f"{portal_base_url()}/portal/{sign(phone)}"
