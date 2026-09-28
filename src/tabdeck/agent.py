from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import ssl
import time
from collections import deque
from pathlib import Path

from .profiles import AGENTS, AgentProfile
from .config import list_projects
from .projects import valid_name
from .registry import Registry
from .status import SHELLS
from .transcript import last_assistant_text

log = logging.getLogger("tabdeck.agent")
KEYS = {"\r", "\x1b", "1", "2", "3", "\x1b[A", "\x1b[B"}
SCREEN_LINES = 60


def _ps_on_tty(tty: str) -> str:
    return subprocess.run(["ps", "-t", os.path.basename(tty), "-o", "pid=,comm="],
                          capture_output=True, text=True, timeout=3).stdout


def claude_on_tty(tty: str, run=_ps_on_tty, process: str = "claude") -> bool:
    """Is the agent (e.g. Claude Code) running on this terminal? iTerm's job name can be a helper
    process it launched (e.g. an MCP server such as azmcp), so look at the processes instead."""
    if not tty:
        return False
    try:
        out = run(tty)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(os.path.basename(line.split(None, 1)[1].strip()) == process
               for line in out.splitlines() if len(line.split(None, 1)) == 2)


class Agent:
    """The Mac side of the hub connection: reports iTerm tabs, runs the hub's commands."""

    def __init__(self, registry: Registry, bridge, projects_dir: Path, name: str = "mac",
                 netbird_ip: str | None = None, gateways=None, hub_server: str = "hub", mac_tabs: bool = True,
                 profile: AgentProfile = AGENTS["claude"], home: str = "~/.tabdeck", session: str = "deck"):
        self.registry, self.bridge, self.projects_dir = registry, bridge, projects_dir
        self.profile, self.home, self.session = profile, home, session
        self.name, self.netbird_ip = name, netbird_ip
        self.gateways, self.hub_server = gateways, hub_server  # tmux -CC tabs of servers (gateways.py)
        self.window_wait = 10.0  # how long a new server window may take to show up as a tab
        self.mac_tabs = mac_tabs  # also report the Mac's own iTerm tabs (off: only server tabs)
        self.queue: deque = deque(maxlen=50)
        self._send = None
        self.claude_on_tty = lambda tty: claude_on_tty(tty, process=profile.process)

    def hello(self) -> dict:
        return {"type": "hello", "agent": self.name, "netbird_ip": self.netbird_ip}

    def snapshot_message(self) -> dict:
        r = self.registry
        return {
            "type": "snapshot",
            "active": f"mac-{r.active}" if r.active else None,
            "projects": list_projects(self.projects_dir),
            "sessions": [{"id": f"mac-{s.session_id}", "title": s.tab_title, "cwd": s.cwd, "job": s.job_name,
                          "tty": s.tty, "pid": s.shell_pid,
                          "screen": "\n".join(s.screen_text.splitlines()[-SCREEN_LINES:]),
                          "urls": [{"url": u} for u in s.urls], "server": s.server, "pane": s.pane}
                         for s in r.sessions.values()
                         if not (s.server and s.server == self.hub_server) and (s.server or self.mac_tabs)],
            # The hub lists its own tmux sessions itself; its tabs here would be duplicates.
            "servers": self.gateways.summary() if self.gateways else [],
        }

    def _local(self, sid: str) -> str:
        if not sid.startswith("mac-") or sid[4:] not in self.registry.sessions:
            raise KeyError(sid)
        return sid[4:]

    async def execute(self, msg: dict) -> dict:
        cid, op, data = msg.get("id"), msg.get("op"), None
        try:
            if op == "create_tab":
                project = str(msg.get("project") or "")
                path = self.projects_dir / project
                if not valid_name(project) or not path.is_dir():
                    raise ValueError("unknown project")
                if msg.get("command") not in (None, "", self.profile.process):
                    raise ValueError("command not allowed")  # the hub may only start the agent here
                data = "mac-" + await self.bridge.create_tab(str(path), self.profile.process)
            elif op == "create_on_server":
                data = await self._create_on_server(str(msg.get("server") or ""), str(msg.get("project") or ""))
            elif op == "focus_pane":
                server, pane = str(msg.get("server") or ""), str(msg.get("pane") or "")
                sid = next((s.session_id for s in self.registry.sessions.values()
                            if server and s.server == server and s.pane == pane), None)
                if sid is None:
                    raise KeyError(pane)
                await self.bridge.focus(sid)
            elif op in ("send_text", "send_keys", "focus", "close", "reply"):
                sid = self._local(str(msg.get("sid") or ""))
                s = self.registry.sessions[sid]
                if op == "send_text":
                    # Allow-list, not deny-list: typed into ssh, python or vim, text would run there.
                    claude = self.profile.is_agent(s.job_name) or (s.hook_seen and s.job_name not in SHELLS) \
                        or self.claude_on_tty(s.tty)
                    if not claude:
                        raise ValueError(f"{self.profile.name} isn't running in that tab")
                    await self.bridge.send_text(sid, str(msg.get("text") or ""))
                elif op == "send_keys":
                    keys = str(msg.get("keys") or "")
                    if keys not in KEYS:
                        raise ValueError("key not allowed")
                    await self.bridge.send_keys(sid, keys)
                elif op == "focus":
                    await self.bridge.focus(sid)
                elif op == "close":
                    await self.bridge.close_session(sid)
                else:
                    data = last_assistant_text(s.transcript_path) if s.transcript_path else ""
            else:
                raise ValueError("unknown command")
            return {"type": "result", "id": cid, "ok": True, "data": data}
        except KeyError:
            return {"type": "result", "id": cid, "ok": False, "error": "unknown tab"}
        except Exception as e:  # noqa: BLE001 - report every failure back to the hub
            return {"type": "result", "id": cid, "ok": False, "error": str(e)}

    async def _create_on_server(self, server: str, project: str) -> str:
        """Start the agent in a new window of the tmux session on a server, through its gateway; returns the tab's id."""
        info = self.gateways.info(server) if self.gateways and valid_name(server) else None
        if info is None:
            raise ValueError("unknown server")
        if not info.online:
            raise ValueError(f"{server} isn't connected")
        if not valid_name(project) or project not in info.projects:
            raise ValueError("unknown project")
        path = f"{info.root}/{project}"
        if "'" in path:
            raise ValueError("unusable project path")
        try:
            taken = set((await self.bridge.tmux_command(info.owner, f"list-windows -t ={self.session} -F '#{{window_name}}'")).split())
        except KeyError:  # the gateway dropped since the last check
            raise ValueError(f"{server} isn't connected") from None
        name, n = project, 2
        while name in taken:
            name, n = f"{project}-{n}", n + 1
        out = await self.bridge.tmux_command(
            info.owner, f"new-window -d -t ={self.session}: -n {name} -c '{path}' -P -F '#{{window_id}}' "
            f"{self.profile.launch(self.home)}")
        window = out.strip().splitlines()[-1] if out.strip() else ""
        end = time.monotonic() + self.window_wait
        while True:
            sid = await self.bridge.session_for_window(info.owner, window) if window else None
            if sid:
                return f"mac-{sid}"
            if time.monotonic() >= end:
                raise ValueError(f"the new window on {server} did not appear")
            await asyncio.sleep(0.25)

    async def hook(self, iterm_session: str, event: dict) -> None:
        sid = iterm_session.split(":")[-1]
        self.registry.apply_hook(iterm_session, event, time.time())
        path = event.get("transcript_path")
        if event.get("hook_event_name") == "Stop" and not event.get("last_assistant_message") and path:
            text = ""
            for _ in range(12):  # the transcript can lag behind the Stop event
                text = last_assistant_text(path)
                if text:
                    break
                await asyncio.sleep(0.25)
            if text:
                event = {**event, "last_assistant_message": text}
        msg = {"type": "hook", "session": f"mac-{sid}", "event": event}
        if self._send is not None:
            try:
                await self._send(json.dumps(msg))
                return
            except Exception:  # noqa: BLE001 - connection dropped: keep it for later
                pass
        self.queue.append(msg)

    async def serve(self, send, recv) -> None:
        """One hub connection: hello, snapshot, queued hooks, then commands until it drops."""
        await send(json.dumps(self.hello()))
        await send(json.dumps(self.snapshot_message()))
        while self.queue:
            await send(json.dumps(self.queue.popleft()))
        self._send = send

        async def pump():
            last, idle = self.registry.version, 0.0
            while True:
                await asyncio.sleep(0.5)
                idle += 0.5
                if self.registry.version != last or idle >= 15:
                    last, idle = self.registry.version, 0.0
                    await send(json.dumps(self.snapshot_message()))

        async def run(msg):
            await send(json.dumps(await self.execute(msg)))

        task = asyncio.create_task(pump())
        running: set[asyncio.Task] = set()
        try:
            while True:
                msg = json.loads(await recv())
                if isinstance(msg, dict) and msg.get("type") == "cmd":
                    t = asyncio.create_task(run(msg))  # a slow command must not hold up the next
                    running.add(t)
                    t.add_done_callback(running.discard)
        finally:
            task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            self._send = None


async def agent_loop(agent: Agent, hub_url: str, token: str, cafile: str | None) -> None:
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    url = hub_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/agent"
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    delays = [1, 2, 5, 10]
    attempt = 0
    while True:
        try:
            async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}, ssl=ctx,
                               ping_interval=20) as ws:
                log.info("connected to hub %s", hub_url)
                attempt = 0
                await agent.serve(ws.send, ws.recv)
        except InvalidStatus as e:
            log.warning("agent token rejected by hub (%s); retrying in 60 s", e)
            await asyncio.sleep(60)
            continue
        except Exception as e:  # noqa: BLE001 - offline, hub restarting, network change
            log.info("hub connection lost: %s", e)
        await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
        attempt += 1
