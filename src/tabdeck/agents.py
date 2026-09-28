from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .files import write_private


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentTokens:
    """Long-lived tokens for agents (the Mac) and the widget, stored hashed on the hub."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text())
            return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def issue(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        hashes = self._load()
        hashes[name] = _hash(token)
        write_private(self.path, json.dumps(hashes))
        return token

    def verify(self, token: str | None) -> str | None:
        if not token:
            return None
        h = _hash(token)
        for name, stored in self._load().items():  # re-read: tokens are issued from the CLI
            if hmac.compare_digest(stored, h):
                return name
        return None
