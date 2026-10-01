from __future__ import annotations

import asyncio
import time
from pathlib import Path

import iterm2

from .profiles import instance_suffix
from .registry import Snapshot
from .status import SHELLS


TMUX_TIMEOUT = 5.0
# Every iTerm API call is a round trip on iTerm's main thread, which also draws and takes your typing:
# polling every detail of every session each second made iTerm lag. So, per session:
DETAILS_EVERY = 5.0  # folder and running program; tty and pid never change, so they're read once
TMUX_RETRY = 10.0  # a tmux tab that matched no server (or two) is matched again this often
PANES = "list-panes -s -t ={session} -F '#{{window_id}} #{{pane_id}} #{{pane_current_path}}'"
# Each instance marks its own gateways, so two instances on one Mac never take over each other's.
GATEWAY_VAR = "user.tabdeck" + instance_suffix().replace("-", "_") + "_gateway"


class ItermBridge:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        # sid -> {"tty", "pid", "path", "job", "screen", "at": when folder and program were read}
        self._meta: dict[str, dict] = {}
        self._tmux_tried: dict[str, float] = {}  # unmatched tmux tab -> when it was last matched
        self.connection: iterm2.Connection | None = None
        self.app: iterm2.App | None = None
        # tmux -CC gateways (gateways.py): gateway session id -> server name. Tabs of a gateway's tmux
        # connection are that server's deck windows.
        self.gateway_servers: dict[str, str] = {}
        self.session = "deck"  # the tmux session gateways attach to (settings: tmux_session)
        self._tmux_tabs: dict[str, tuple[str, str, str, str]] = {}  # sid -> (server, owner, window, pane)

    @property
    def connected(self) -> bool:
        return self.app is not None

    async def connect(self) -> None:
        self.connection = await iterm2.Connection.async_create()
        self.app = await iterm2.async_get_app(self.connection)

    async def reset(self) -> None:
        conn, self.connection, self.app = self.connection, None, None
        if conn is not None:
            try:
                await conn.websocket.close()
            except Exception:  # noqa: BLE001 - already broken
                pass

    def _gone(self, sid: str) -> bool:
        return self.app is None or self.app.get_session_by_id(sid) is None

    def _tabs(self) -> list[iterm2.Tab]:
        assert self.app is not None
        return [t for w in self.app.terminal_windows for t in w.tabs]

    def _sessions(self) -> list[iterm2.Session]:
        return [s for t in self._tabs() for s in t.sessions]

    async def _tmux_connections(self) -> dict:
        if self.connection is None:
            return {}
        return {c.connection_id: c for c in await iterm2.async_get_tmux_connections(self.connection)}

    async def _owners(self) -> dict[str, str]:
        """tmux connection id -> the id of the session that owns it (the gateway)."""
        owners = {}
        for cid, c in (await self._tmux_connections()).items():
            owner = c.owning_session
            if owner is not None:
                owners[cid] = owner.session_id
        return owners

    async def live_owners(self) -> set[str]:
        """Sessions that currently own a tmux connection: gateways that are attached."""
        return set((await self._owners()).values())

    async def _connection_of(self, owner: str):
        for c in (await self._tmux_connections()).values():
            if c.owning_session is not None and c.owning_session.session_id == owner:
                return c
        raise KeyError(owner)

    async def tmux_command(self, owner: str, command: str) -> str:
        """Run a tmux command over a gateway's control connection (no extra ssh). The command must print
        something: iTerm waits for output."""
        return await asyncio.wait_for((await self._connection_of(owner)).async_send_command(command), TMUX_TIMEOUT)

    async def _resolve_tmux_tabs(self, retry_now: bool = False) -> dict[str, tuple[str, str, str, str]]:
        """Which gateway each tmux tab belongs to. iTerm doesn't say (a tab's tmux connection id is empty),
        so match its window, pane and folder against each gateway's own pane list; remember matches."""
        live = set(self.gateway_servers)
        self._tmux_tabs = {sid: v for sid, v in self._tmux_tabs.items() if v[1] in live and not self._gone(sid)}
        pending = []
        for t in self._tabs():
            if str(t.tmux_window_id) in ("None", "", "-1"):
                continue  # not a tmux tab
            for s in t.sessions:
                if s.session_id not in self._tmux_tabs and s.session_id not in live \
                        and (retry_now or self._clock() - self._tmux_tried.get(s.session_id, float("-inf")) >= TMUX_RETRY):
                    pending.append((t, s))
        if not pending or not live:
            return self._tmux_tabs
        known: dict[tuple, list] = {}
        for owner, server in list(self.gateway_servers.items()):
            try:
                out = await self.tmux_command(owner, PANES.format(session=self.session))
            except Exception:  # noqa: BLE001 - that gateway is going away
                continue
            for line in out.splitlines():
                window, _, rest = line.partition(" ")
                pane, _, path = rest.partition(" ")
                known.setdefault((window.lstrip("@"), pane, path), []).append((server, owner))
        for t, s in pending:
            self._tmux_tried[s.session_id] = self._clock()
            p = await s.async_get_variable("tmuxWindowPane")
            if p in (None, ""):
                continue
            pane, window = "%" + str(p).lstrip("%"), str(t.tmux_window_id).lstrip("@")
            hits = known.get((window, pane, await s.async_get_variable("path") or ""), [])
            if len(hits) == 1:  # the same window, pane and folder on two servers: leave it untagged
                self._tmux_tabs[s.session_id] = (hits[0][0], hits[0][1], window, pane)
        return self._tmux_tabs

    async def hide_window(self, owner: str, window_id: str) -> None:
        await (await self._connection_of(owner)).async_set_tmux_window_visible(window_id.lstrip("@"), False)

    async def session_for_window(self, owner: str, window_id: str) -> str | None:
        """The iTerm session showing tmux window `window_id` (e.g. "@12") of that gateway, once its tab exists."""
        want = str(window_id).lstrip("@")
        for sid, (_, own, window, _) in (await self._resolve_tmux_tabs(retry_now=True)).items():
            if own == owner and window == want:
                return sid
        return None

    async def open_gateway(self, command: str, name: str) -> str:
        """A new tab in the home folder running `command` (an ssh + tmux -CC gateway), marked with the
        server's name so a restarted agent finds it again. The tab the user was in stays selected."""
        before = await self.active_session()
        sid = await self.create_tab(str(Path.home()), command)
        await self._session(sid).async_set_variable(GATEWAY_VAR, name)
        if before and before != sid and not self._gone(before):
            try:
                await self._session(before).async_activate(select_tab=True, order_window_front=False)
            except Exception:  # noqa: BLE001 - closed meanwhile
                pass
        return sid

    async def gateway_name(self, sid: str) -> str | None:
        """The server a gateway session was opened for (set by open_gateway), or None."""
        try:
            return await self._session(sid).async_get_variable(GATEWAY_VAR) or None
        except KeyError:
            return None

    def alive(self, sid: str) -> bool:
        return not self._gone(sid)

    async def apply_tmux_prefs(self) -> None:
        """tmux windows open as tabs in the current window, with the gateway session hidden."""
        assert self.connection is not None
        keys = iterm2.PreferenceKey
        await iterm2.async_set_preference(self.connection, keys.OPEN_TMUX_WINDOWS_IN, 2)
        await iterm2.async_set_preference(self.connection, keys.AUTO_HIDE_TMUX_CLIENT_SESSION, True)
        await iterm2.async_set_preference(self.connection, keys.TMUX_DASHBOARD_LIMIT, 1000)

    def _session(self, sid: str) -> iterm2.Session:
        s = self.app.get_session_by_id(sid) if self.app else None
        if s is None:
            raise KeyError(sid)
        return s

    async def snapshot(self) -> list[Snapshot]:
        tagged = await self._resolve_tmux_tabs() if self.gateway_servers else {}
        snaps = []
        sessions = self._sessions()
        alive = {s.session_id for s in sessions}
        for cache in (self._meta, self._tmux_tried):
            for sid in [sid for sid in cache if sid not in alive]:
                del cache[sid]
        for s in sessions:
            if s.session_id in self.gateway_servers:
                continue  # the gateway itself: an ssh session, not a tab of its own
            try:
                snaps.append(await self._snapshot_one(s, tagged.get(s.session_id)))
            except Exception:
                if self._gone(s.session_id):
                    continue  # closed while we were reading it
                raise
        return snaps

    async def _snapshot_one(self, s: iterm2.Session, tmux: tuple | None = None) -> Snapshot:
        now = self._clock()
        m = self._meta.get(s.session_id)
        fresh = m is None or now - m["at"] >= DETAILS_EVERY
        if m is None:
            m = {"tty": await s.async_get_variable("tty") or "",
                 "pid": int(await s.async_get_variable("pid") or 0), "screen": ""}
        if fresh:
            m["path"] = await s.async_get_variable("path") or ""
            m["job"] = await s.async_get_variable("jobName") or ""
            m["at"] = now
        if fresh or m["job"] not in SHELLS:  # an idle shell prompt doesn't change on its own
            contents = await s.async_get_screen_contents()
            m["screen"] = "\n".join(contents.line(i).string for i in range(contents.number_of_lines)).rstrip()
        self._meta[s.session_id] = m
        server, pane = (tmux[0], tmux[3]) if tmux else ("", "")
        return Snapshot(
            session_id=s.session_id,
            tab_title=s.name or "",
            cwd=m["path"],
            job_name=m["job"],
            tty=m["tty"],
            shell_pid=m["pid"],
            screen_text=m["screen"],
            server=server,
            pane=pane,
        )

    async def _send(self, sid: str, *chunks: str) -> None:
        s = self._session(sid)
        try:
            for i, chunk in enumerate(chunks):
                if i:
                    await asyncio.sleep(0.05)
                await s.async_send_text(chunk)
        except Exception as e:
            if self._gone(sid):
                raise KeyError(sid) from e
            raise

    async def active_session(self) -> str | None:
        window = self.app.current_terminal_window if self.app else None
        tab = window.current_tab if window else None
        session = tab.current_session if tab else None
        return session.session_id if session else None

    async def send_text(self, sid: str, text: str) -> None:
        await self._send(sid, " ".join(text.splitlines()), "\r")

    async def send_keys(self, sid: str, keys: str) -> None:
        await self._send(sid, keys)

    async def create_tab(self, cwd: str, command: str | None) -> str:
        assert self.app is not None and self.connection is not None
        # Open the tab directly in cwd: a `cd` typed while the shell is still
        # starting does not update iTerm's `path` variable.
        profile = iterm2.LocalWriteOnlyProfile()
        profile.set_initial_directory_mode(
            iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(cwd)
        window = self.app.current_terminal_window
        if window is None:
            window = await iterm2.Window.async_create(
                self.connection, profile_customizations=profile)
            session = window.tabs[0].sessions[0]
        else:
            tab = await window.async_create_tab(profile_customizations=profile)
            session = tab.sessions[0]
        if command:
            await asyncio.sleep(0.5)
            await session.async_send_text(command + "\r")
        return session.session_id

    async def focus(self, sid: str) -> None:
        """Select the session's tab and window, and bring iTerm in front of other apps."""
        s = self._session(sid)
        try:
            await s.async_activate(select_tab=True, order_window_front=True)
            await self.app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        except Exception as e:
            if self._gone(sid):
                raise KeyError(sid) from e
            raise

    async def close_session(self, sid: str) -> None:
        await self._session(sid).async_close(force=True)
