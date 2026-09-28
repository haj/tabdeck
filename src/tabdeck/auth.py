from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from .files import write_private

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
# Set by reverse proxies and tunnels (nginx, Caddy, Cloudflare, ngrok). A request carrying one came from elsewhere,
# even though it reaches us from 127.0.0.1, so it gets no localhost trust.
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip", "x-forwarded-host", "true-client-ip")


def is_local(host: str) -> bool:
    return host in LOCAL_HOSTS


def is_local_request(conn) -> bool:
    """A request made on this machine itself: from localhost, and not relayed by a proxy or tunnel."""
    host = conn.client.host if conn.client else ""
    return is_local(host) and not any(h in conn.headers for h in PROXY_HEADERS)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    CODE_TTL = 600

    def __init__(self, tokens_file: Path):
        self.file = tokens_file
        self._codes: dict[str, float] = {}
        self._hashes: set[str] = set()
        if self.file.exists():
            try:
                self._hashes = set(json.loads(self.file.read_text()))
            except ValueError:
                self._hashes = set()

    def _save(self) -> None:
        write_private(self.file, json.dumps(sorted(self._hashes)))

    def new_pairing_code(self, now: float) -> str:
        self._codes = {c: exp for c, exp in self._codes.items() if exp >= now}
        code = secrets.token_urlsafe(16)
        self._codes[code] = now + self.CODE_TTL
        return code

    def redeem(self, code: str, now: float) -> str | None:
        expires = self._codes.pop(code, None)
        if expires is None or expires < now:
            return None
        token = secrets.token_urlsafe(32)
        self._hashes.add(_hash(token))
        self._save()
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token) and _hash(token) in self._hashes

    def revoke_all(self) -> None:
        self._hashes.clear()
        self._save()
