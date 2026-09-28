from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

URL_RE = re.compile(
    r"(https?)://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\d{1,3}(?:\.\d{1,3}){3})"
    r":(\d{2,5})(/[^\s'\"<>)\]]*)?"
)
WILDCARD_HOSTS = {"*", "0.0.0.0", "[::]", "::"}


@dataclass(frozen=True)
class Listener:
    pid: int
    host: str
    port: int


def extract_urls(text: str) -> list[str]:
    out: list[str] = []
    for m in URL_RE.finditer(text):
        path = (m.group(3) or "/").rstrip(".,;:!?") or "/"
        url = f"{m.group(1)}://localhost:{int(m.group(2))}{path}"
        if url not in out:
            out.append(url)
    return out


def url_port(url: str) -> int | None:
    try:
        return urlsplit(url).port
    except ValueError:
        return None


def parse_ps(output: str) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        children.setdefault(ppid, []).append(pid)
    return children


def descendants(children: dict[int, list[int]], root: int) -> set[int]:
    seen = {root}
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def parse_lsof(output: str) -> list[Listener]:
    result: list[Listener] = []
    pid = None
    for line in output.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None and ":" in line:
            host, _, port = line[1:].rpartition(":")
            if port.isdigit():
                result.append(Listener(pid, host, int(port)))
    return result


def session_urls(screen_urls: Iterable[str], listeners: list[Listener], pids: set[int],
                 pins: Iterable[str] = (), hidden: Iterable[str] = ()) -> list[str]:
    listening = {l.port for l in listeners}
    urls: list[str] = list(pins)
    for u in screen_urls:
        if url_port(u) in listening and u not in urls:
            urls.append(u)
    for l in listeners:
        if l.pid in pids and all(url_port(u) != l.port for u in urls):
            urls.append(f"http://localhost:{l.port}/")
    hidden_set = set(hidden)
    return [u for u in urls if u not in hidden_set]


def phone_href(url: str, ip: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, f"{ip}:{parts.port}", parts.path, parts.query, parts.fragment))


def needs_forward(port: int, listeners: list[Listener], ip: str) -> bool:
    return not any(l.port == port and (l.host in WILDCARD_HOSTS or l.host == ip) for l in listeners)
