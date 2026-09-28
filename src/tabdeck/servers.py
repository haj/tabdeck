from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .projects import valid_name

log = logging.getLogger(__name__)
# [user@]host, never an option: it goes on an ssh command line.
SSH_TARGET = re.compile(r"^([A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Server:
    """A server whose tmux session (default "deck") the Mac shows as iTerm tabs (see gateways.py)."""
    name: str
    ssh: str  # ssh target: user@host or a ~/.ssh/config alias
    host: str  # address probed on port 22 to see whether it is reachable
    projects: str = "Projects"  # projects folder, relative to the home folder


def load_servers(path: Path) -> list[Server]:
    """<data dir>/servers.json: [{"name", "ssh", "host"?, "projects"?}]. Invalid entries are skipped."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    servers = []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict):
            continue
        name, ssh = str(e.get("name") or ""), str(e.get("ssh") or "")
        projects = str(e.get("projects") or "Projects")
        if not valid_name(name) or not SSH_TARGET.match(ssh) or "'" in projects or projects.startswith("/"):
            log.warning("skipping server entry %r in %s", e, path)
            continue
        host = str(e.get("host") or ssh.split("@")[-1])
        servers.append(Server(name, ssh, host, projects))
    return servers
