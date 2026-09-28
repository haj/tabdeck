from __future__ import annotations

import asyncio
from dataclasses import replace

from .projects import valid_name
from .registry import Snapshot


class AgentOffline(Exception):
    pass


class AgentTimeout(Exception):
    pass


class AgentError(Exception):
    pass


class RemoteSource:
    """Sessions of a remote agent (the Mac), fed by its WebSocket; commands go back over it."""

    prefix = "mac-"

    def __init__(self, name: str = "mac", timeout: float = 5.0):
        self.name, self.timeout = name, timeout
        self.create_timeout = 20.0  # opening a tab (or a window) takes longer
        self.online, self.connected = False, True
        self.projects: list[str] = []
        self.servers: list[dict] = []  # servers the agent keeps tmux gateways to: {name, online, projects}
        self._send = None
        self._sessions: list[Snapshot] = []
        self._active: str | None = None
        self._url_host: str | None = None
        self._next = 0
        self._waiting: dict[int, asyncio.Future] = {}

    async def connect(self) -> None:
        pass

    async def reset(self) -> None:
        pass

    def attach(self, send, hello: dict) -> None:
        self._send, self.online = send, True
        self._url_host = hello.get("netbird_ip") or None

    def detach(self, send=None) -> None:
        """The agent's socket closed. With `send`, only if that socket is still the current one:
        after a laptop sleep the agent reconnects before the hub notices the old socket died."""
        if send is not None and send is not self._send:
            return
        self._send, self.online, self._active = None, False, None
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(AgentOffline())
        self._waiting.clear()

    def on_snapshot(self, msg: dict) -> None:
        self._active = msg.get("active")
        self.projects = [p for p in msg.get("projects", []) if isinstance(p, str) and valid_name(p)]
        self.servers = [{"name": v["name"], "online": v.get("online") is True,
                         "projects": [p for p in v.get("projects") or [] if isinstance(p, str) and valid_name(p)]}
                        for v in msg.get("servers", []) if isinstance(v, dict) and isinstance(v.get("name"), str)
                        and valid_name(v["name"])]
        snaps = []
        for s in msg.get("sessions", []):
            sid = str(s.get("id", ""))
            if not sid.startswith(self.prefix):
                continue
            urls = tuple(u["url"] for u in s.get("urls", []) if isinstance(u, dict) and isinstance(u.get("url"), str))
            pid = s.get("pid")
            snaps.append(Snapshot(sid, str(s.get("title", "")), str(s.get("cwd", "")), str(s.get("job", "")),
                                  str(s.get("tty", "")), pid if isinstance(pid, int) else 0,
                                  str(s.get("screen", "")), urls=urls, url_host=self._url_host, remote=True,
                                  server=str(s.get("server") or ""), pane=str(s.get("pane") or "")))
        self._sessions = snaps

    def on_result(self, msg: dict) -> None:
        fut = self._waiting.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if msg.get("ok"):
            fut.set_result(msg.get("data"))
        elif msg.get("error") == "unknown tab":
            fut.set_exception(KeyError(msg.get("error")))
        else:
            fut.set_exception(AgentError(str(msg.get("error") or "the Mac could not do that")))

    async def snapshot(self) -> list[Snapshot]:
        return [replace(s, offline=not self.online) for s in self._sessions]

    async def active_session(self) -> str | None:
        return self._active if self.online else None

    async def command(self, op: str, timeout: float | None = None, **args):
        if not self.online or self._send is None:
            raise AgentOffline()
        self._next += 1
        cid = self._next
        fut = asyncio.get_running_loop().create_future()
        self._waiting[cid] = fut
        try:
            await self._send({"type": "cmd", "id": cid, "op": op, **args})
        except Exception:  # noqa: BLE001 - the socket is closing
            self._waiting.pop(cid, None)
            raise AgentOffline() from None
        try:
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError:
            self._waiting.pop(cid, None)
            raise AgentTimeout() from None

    async def send_text(self, sid: str, text: str) -> None:
        await self.command("send_text", sid=sid, text=text)

    async def send_keys(self, sid: str, keys: str) -> None:
        await self.command("send_keys", sid=sid, keys=keys)

    async def focus(self, sid: str) -> None:
        await self.command("focus", sid=sid)

    async def close_session(self, sid: str) -> None:
        await self.command("close", sid=sid)

    async def reply_text(self, sid: str) -> str:
        return str(await self.command("reply", sid=sid) or "")

    async def create_on_server(self, server: str, project: str) -> str:
        return str(await self.command("create_on_server", timeout=self.create_timeout, server=server, project=project))

    async def focus_pane(self, server: str, pane: str) -> None:
        await self.command("focus_pane", server=server, pane=pane)

    async def create_tab(self, project: str, command: str | None) -> str:
        return str(await self.command("create_tab", timeout=self.create_timeout, project=project, command=command))
