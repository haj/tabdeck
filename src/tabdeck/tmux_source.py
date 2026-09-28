from __future__ import annotations

import asyncio
import os
import re

from .registry import Snapshot

FORMAT = "#{pane_id}\t#{window_name}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_tty}\t#{pane_pid}"
DECK = "deck"  # default tmux session; each agent session is a window in it (settings: tmux_session)
KEEPALIVE = "_keep"  # deck's plain-shell window (scripts/deck-start.sh); not a Claude session
KEYS = {"\r": "Enter", "\x1b": "Escape", "\x1b[B": "Down", "\x1b[A": "Up"}


async def _run_tmux(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "tmux", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


class TmuxSource:
    """Agent sessions in the windows of one tmux session (default "deck") on this machine (the always-on hub)."""

    def __init__(self, run=None, server: str = "hub", session: str = DECK) -> None:
        self._run = run or _run_tmux
        self.server, self.session = server, session
        self.connected = True

    async def connect(self) -> None:
        self.connected = True

    async def reset(self) -> None:
        pass

    @staticmethod
    def to_sid(pane: str) -> str:
        return "tmux-" + pane.strip().lstrip("%")

    @staticmethod
    def to_pane(sid: str) -> str:
        if not sid.startswith("tmux-") or not sid[5:].isdigit():
            raise KeyError(sid)
        return "%" + sid[5:]

    async def snapshot(self) -> list[Snapshot]:
        code, out = await self._run("list-panes", "-s", "-t", f"={self.session}", "-F", FORMAT)
        if code != 0:
            return []  # no tmux server or no deck yet
        snaps = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 6:
                continue
            pane, name, cwd, cmd, tty, pid = parts
            if name == KEEPALIVE:
                continue  # keeps deck alive; not a Claude session
            ok, screen = await self._run("capture-pane", "-p", "-t", pane)
            snaps.append(Snapshot(self.to_sid(pane), name, cwd, cmd, tty,
                                  int(pid) if pid.isdigit() else 0, screen.rstrip() if ok == 0 else "",
                                  server=self.server, pane=pane))
        return snaps

    async def active_session(self) -> str | None:
        return None  # a server has no focused tab

    async def _pane(self, sid: str) -> str:
        pane = self.to_pane(sid)
        code, out = await self._run("list-panes", "-s", "-t", f"={self.session}", "-F", "#{pane_id}")
        if code != 0 or pane not in out.split():
            raise KeyError(sid)
        return pane

    async def send_text(self, sid: str, text: str) -> None:
        pane = await self._pane(sid)
        await self._run("send-keys", "-t", pane, "-l", " ".join(text.splitlines()))
        await asyncio.sleep(0.05)
        await self._run("send-keys", "-t", pane, "Enter")

    async def send_keys(self, sid: str, keys: str) -> None:
        pane = await self._pane(sid)
        if keys in KEYS:
            await self._run("send-keys", "-t", pane, KEYS[keys])
        else:
            await self._run("send-keys", "-t", pane, "-l", keys)

    async def _unique_name(self, folder: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_-]", "-", folder) or "session"
        code, out = await self._run("list-windows", "-t", f"={self.session}", "-F", "#{window_name}")
        taken = set(out.split()) if code == 0 else set()
        name, n = base, 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        return name

    async def create_tab(self, cwd: str, command: str | None) -> str:
        code, _ = await self._run("has-session", "-t", f"={self.session}")
        if code != 0:
            await self._run("new-session", "-d", "-s", self.session, "-n", KEEPALIVE)
        name = await self._unique_name(os.path.basename(cwd.rstrip("/")))
        args = ["new-window", "-d", "-t", f"={self.session}:", "-n", name, "-c", cwd, "-P", "-F", "#{pane_id}"]
        if command:
            args.append(command)
        code, out = await self._run(*args)
        if code != 0:
            raise RuntimeError(out.strip() or "tmux new-window failed")
        return self.to_sid(out.strip())

    async def focus(self, sid: str) -> None:
        await self._pane(sid)  # nothing to bring to front on a server; just validate

    async def close_session(self, sid: str) -> None:
        await self._run("kill-window", "-t", await self._pane(sid))
