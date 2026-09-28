from __future__ import annotations

import asyncio
import json
import os
import shutil
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import list_projects

# Never prompt (no terminal on the hub); learn a host key on first contact, refuse changed ones.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_name(name: str) -> bool:
    return bool(NAME.match(name)) and ".." not in name


def load_remotes(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and valid_name(k)} if isinstance(data, dict) else {}


def all_projects(projects_dir: Path, remotes: dict[str, str]) -> list[str]:
    local = list_projects(projects_dir)
    return local + sorted(n for n in remotes if n not in local)


async def _run_git(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **GIT_ENV})
    try:
        out, _ = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()  # timed out: do not leave git running
        raise
    return proc.returncode or 0, out.decode(errors="replace")


async def clone(remote: str, dest: Path, run=None, timeout: float = 120) -> str | None:
    """git clone remote into dest. None on success, else a short error message."""
    run = run or _run_git
    existed = dest.exists()
    try:
        code, out = await asyncio.wait_for(run("clone", "--", remote, str(dest)), timeout)
    except asyncio.TimeoutError:
        if not existed:
            shutil.rmtree(dest, ignore_errors=True)  # never start Claude in a half-cloned repo
        return "the clone took too long"
    if code == 0:
        return None
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return lines[-1] if lines else f"git exited with {code}"


def _without_credentials(url: str) -> str:
    """https://user:token@host/x -> https://host/x (never copy secrets to the hub)."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and "@" in parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[1], parts.path, parts.query, parts.fragment))
    return url


def project_remotes(projects_dir: Path) -> dict[str, str]:
    """origin URL of every git repo directly in projects_dir (run on the Mac to seed the hub)."""
    out: dict[str, str] = {}
    for name in list_projects(projects_dir):
        r = subprocess.run(["git", "-C", str(projects_dir / name), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() and valid_name(name):
            out[name] = _without_credentials(r.stdout.strip())
    return out
