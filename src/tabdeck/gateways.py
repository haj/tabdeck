from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .projects import valid_name
from .servers import Server

log = logging.getLogger(__name__)

INTERVAL = 15.0  # seconds between checks
GRACE = 30.0  # a gateway must attach (tmux -CC) within this long
STABLE = 60.0  # attached this long: earlier failures are forgotten
BACKOFF, MAX_BACKOFF = 15.0, 600.0
LIST_EVERY, LIST_RETRY = 600.0, 60.0


def gateway_command(server: Server, session: str = "deck", home: str = "~/.tabdeck") -> str:
    """Typed into a new iTerm tab: ssh to the server and attach iTerm's tmux integration to deck.
    `exec` ends the tab with the connection. If deck is gone (the server rebooted and nothing restored it),
    deck-start.sh brings back the saved windows first; otherwise deck is left exactly as it is."""
    remote = (f"PATH=$HOME/.local/bin:$PATH; tmux has-session -t ={session} 2>/dev/null || "
              f"{{ [ -x {home}/deck-start.sh ] && {home}/deck-start.sh; }}; exec tmux -CC new -A -s {session}")
    return f"exec ssh -t {server.ssh} '{remote}'"


async def tcp_probe(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def ssh_works(server: Server) -> bool:
    """Can we log in without a prompt? A gateway tab asking for a password would catch the user's typing."""
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", server.ssh, "true",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(proc.wait(), 15) == 0
    except asyncio.TimeoutError:
        proc.kill()
        return False


async def reachable(server: Server) -> bool:
    if not await tcp_probe(server.host):
        return False
    if await ssh_works(server):
        return True
    log.warning("ssh to %s (%s) needs a password or key; not opening a gateway", server.name, server.ssh)
    return False


async def ssh_list_projects(server: Server) -> tuple[str, list[str]]:
    """The absolute projects folder on the server and the project folders in it."""
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", server.ssh,
        f"cd '{server.projects}' && pwd && ls -1p",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"ssh {server.ssh} exited {proc.returncode}")
    lines = out.decode(errors="replace").splitlines()
    if not lines:
        raise RuntimeError("no output")
    names = [l[:-1] for l in lines[1:] if l.endswith("/") and valid_name(l[:-1])]
    return lines[0].strip(), names


@dataclass
class ServerState:
    name: str
    online: bool = False  # its gateway is attached: deck's windows are tabs
    projects: list[str] = field(default_factory=list)
    root: str = ""  # absolute projects folder on the server
    owner: str | None = None  # the gateway's iTerm session id
    opened_at: float = 0.0
    live_since: float | None = None
    failures: int = 0
    next_try: float = 0.0
    listed_at: float | None = None
    hidden: bool = False


class GatewayManager:
    """One tmux -CC gateway per reachable server, reopened after drops, with backoff after failures."""

    def __init__(self, servers: list[Server], bridge, probe=reachable, lister=ssh_list_projects,
                 clock=time.monotonic, on_change=None, session: str = "deck", home: str = "~/.tabdeck"):
        self.session, self.home = session, home
        self.servers, self.bridge, self.probe, self.lister, self.clock = servers, bridge, probe, lister, clock
        self.on_change = on_change or (lambda: None)
        self.states = {s.name: ServerState(s.name) for s in servers}

    def info(self, name: str) -> ServerState | None:
        return self.states.get(name)

    def summary(self) -> list[dict]:
        return [{"name": s.name, "online": s.online, "projects": list(s.projects)} for s in self.states.values()]

    async def tick(self) -> None:
        before = self.summary()
        live = await self.bridge.live_owners()
        await self._adopt(live)
        for server in self.servers:
            try:
                await self._check(server, self.states[server.name], live)
            except Exception:  # noqa: BLE001 - one server's trouble must not stop the others
                log.exception("gateway check for %s failed", server.name)
        if self.summary() != before:
            self.on_change()

    async def _adopt(self, live: set[str]) -> None:
        """Gateways opened before the agent (re)started: take them over instead of opening duplicates."""
        for owner in live - set(self.bridge.gateway_servers):
            name = await self.bridge.gateway_name(owner)
            st = self.states.get(name) if name else None
            if st is not None and st.owner is None:
                self.bridge.gateway_servers[owner] = name
                st.owner, st.opened_at, st.live_since, st.hidden = owner, self.clock(), None, False
                log.info("reusing the open gateway to %s", name)

    async def _check(self, server: Server, st: ServerState, live: set[str]) -> None:
        now = self.clock()
        if st.owner is not None:
            if st.owner in live:
                if st.live_since is None:
                    st.live_since = now
                    log.info("gateway to %s attached", server.name)
                if now - st.live_since >= STABLE:
                    st.failures = 0
                st.online = True
                if not st.hidden:
                    await self._hide_keepalive(st)
            elif st.live_since is not None or not self.bridge.alive(st.owner) or now - st.opened_at >= GRACE:
                await self._drop(server, st, now)
        if st.owner is None and now >= st.next_try and await self.probe(server):
            st.owner = await self.bridge.open_gateway(gateway_command(server, self.session, self.home), server.name)
            self.bridge.gateway_servers[st.owner] = server.name
            st.opened_at, st.live_since, st.hidden = now, None, False
            log.info("opening gateway to %s", server.name)
        if st.online and (st.listed_at is None or now - st.listed_at >= LIST_EVERY):
            try:
                st.root, st.projects = await self.lister(server)
                st.listed_at = now
            except Exception as e:  # noqa: BLE001 - try again soon
                log.warning("listing projects on %s failed: %s", server.name, e)
                st.listed_at = now - LIST_EVERY + LIST_RETRY

    async def _drop(self, server: Server, st: ServerState, now: float) -> None:
        owner, was_live = st.owner, st.live_since is not None
        if self.bridge.alive(owner):
            try:
                await self.bridge.close_session(owner)
            except Exception:  # noqa: BLE001 - already closing
                pass
        self.bridge.gateway_servers.pop(owner, None)
        if not (was_live and now - st.live_since >= STABLE):
            st.failures += 1
        st.next_try = now + (min(BACKOFF * 2 ** (st.failures - 1), MAX_BACKOFF) if st.failures else 0)
        st.owner, st.online, st.live_since = None, False, None
        log.info("gateway to %s %s; next attempt in %.0f s", server.name, "dropped" if was_live else "failed",
                 st.next_try - now)

    async def _hide_keepalive(self, st: ServerState) -> None:
        try:
            out = await self.bridge.tmux_command(st.owner, f"list-windows -t ={self.session} -F '#{{window_id}} #{{window_name}}'")
            for line in out.splitlines():
                wid, _, name = line.partition(" ")
                if name == "_keep":
                    await self.bridge.hide_window(st.owner, wid)
            st.hidden = True
        except Exception as e:  # noqa: BLE001 - cosmetic; retried next check
            log.debug("could not hide _keep on %s: %s", st.name, e)


async def gateway_loop(manager: GatewayManager, bridge) -> None:
    prefs = False
    while True:
        try:
            if bridge.connected:
                if not prefs:
                    await bridge.apply_tmux_prefs()
                    prefs = True
                await manager.tick()
            else:
                prefs = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - iTerm restarting
            log.exception("gateway loop")
        await asyncio.sleep(INTERVAL)
