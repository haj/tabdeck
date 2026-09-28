from __future__ import annotations

import os
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Write a file that holds secrets: owner-only from the moment it exists, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)  # in case tmp already existed with wider permissions
    os.replace(tmp, path)
