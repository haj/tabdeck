from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class CompositeSource:
    """The hub's sessions: the local source (tmux) plus remote agents, dispatched by id prefix."""

    def __init__(self, local, remotes: dict, hub_server: str = "hub"):
        self.local, self.remotes, self.hub_server = local, remotes, hub_server

    @property
    def connected(self) -> bool:
        return self.local.connected

    async def connect(self) -> None:
        await self.local.connect()

    async def reset(self) -> None:
        await self.local.reset()

    def _for(self, sid: str):
        for r in self.remotes.values():
            if sid.startswith(r.prefix):
                return r
        return self.local

    async def snapshot(self):
        snaps = list(await self.local.snapshot())
        for r in self.remotes.values():
            snaps += await r.snapshot()
        return snaps

    async def active_session(self):
        for r in self.remotes.values():
            if (a := await r.active_session()) is not None:
                return a
        return await self.local.active_session()

    async def send_text(self, sid, text):
        await self._for(sid).send_text(sid, text)

    async def send_keys(self, sid, keys):
        await self._for(sid).send_keys(sid, keys)

    async def focus(self, sid):
        source = self._for(sid)
        await source.focus(sid)
        if source is self.local:
            # A hub session is also a tab on the Mac (tmux -CC): bring that one forward, if it is there.
            pane = "%" + sid.rsplit("-", 1)[-1]
            for r in self.remotes.values():
                if r.online and hasattr(r, "focus_pane"):
                    try:
                        await r.focus_pane(self.hub_server, pane)
                    except Exception as e:  # noqa: BLE001 - best effort: the tab may not be open there
                        log.debug("no Mac tab for %s: %s", sid, e)

    async def close_session(self, sid):
        await self._for(sid).close_session(sid)

    async def create_tab(self, cwd, command):
        return await self.local.create_tab(cwd, command)

    async def create_on_server(self, name, server, project):
        return await self.remotes[name].create_on_server(server, project)

    async def create_remote(self, name, project, command):
        return await self.remotes[name].create_tab(project, command)

    async def reply_text(self, sid):
        source = self._for(sid)
        return await source.reply_text(sid) if source is not self.local else None
