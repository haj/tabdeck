from tabdeck.composite import CompositeSource
from tabdeck.registry import Snapshot
from tabdeck.remote_source import RemoteSource


class Local:
    connected = True

    def __init__(self):
        self.calls = []

    async def snapshot(self):
        return [Snapshot("tmux-1", "a", "/p/a", "claude", "", 1, "")]

    async def active_session(self):
        return None

    async def send_text(self, sid, text):
        self.calls.append(("send_text", sid, text))

    async def create_tab(self, cwd, command):
        return "tmux-2"


async def test_dispatch_by_prefix():
    local, remote = Local(), RemoteSource()
    sent = []

    async def send(m):
        sent.append(m)
        remote.on_result({"type": "result", "id": m["id"], "ok": True, "data": "mac-NEW" if m["op"] == "create_tab" else None})
    remote.attach(send, {})
    remote.on_snapshot({"type": "snapshot", "active": "mac-A", "sessions": [
        {"id": "mac-A", "title": "t", "cwd": "/p/F", "job": "claude", "tty": "", "pid": 1, "screen": ""}]})
    c = CompositeSource(local, {"mac": remote})
    assert [s.session_id for s in await c.snapshot()] == ["tmux-1", "mac-A"]
    assert await c.active_session() == "mac-A"
    await c.send_text("tmux-1", "hi")
    await c.send_text("mac-A", "yo")
    assert local.calls == [("send_text", "tmux-1", "hi")] and sent[0]["op"] == "send_text"
    assert await c.create_tab("/p/x", "claude") == "tmux-2"
    assert await c.create_remote("mac", "Atlas", "claude") == "mac-NEW"
    assert await c.reply_text("tmux-1") is None


async def test_focusing_a_hub_session_also_brings_its_mac_tab_forward():
    class L(Local):
        async def focus(self, sid):
            self.calls.append(("focus", sid))
    local, remote = L(), RemoteSource()
    sent = []

    async def send(m):
        sent.append(m)
        remote.on_result({"type": "result", "id": m["id"], "ok": False, "error": "unknown tab"})
    c = CompositeSource(local, {"mac": remote}, hub_server="hub1")
    await c.focus("tmux-3")  # Mac offline: local only, no error
    remote.attach(send, {})
    await c.focus("tmux-3")  # the Mac has no such tab: still fine
    assert local.calls == [("focus", "tmux-3"), ("focus", "tmux-3")]
    assert [(m["op"], m["server"], m["pane"]) for m in sent] == [("focus_pane", "hub1", "%3")]
