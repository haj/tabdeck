import asyncio

import pytest

from tabdeck.remote_source import AgentError, AgentOffline, AgentTimeout, RemoteSource

SNAP = {"type": "snapshot", "active": "mac-A", "projects": ["Atlas", "../x"],
        "sessions": [{"id": "mac-A", "title": "claude", "cwd": "/p/Atlas", "job": "claude", "tty": "/dev/ttys1",
                      "pid": 5, "screen": "hi", "urls": [{"url": "http://localhost:5173/"}]},
                     {"id": "tmux-9", "title": "x", "cwd": "/", "job": "zsh", "tty": "", "pid": 1, "screen": ""}]}


async def test_snapshot_online_then_offline():
    r = RemoteSource()
    sent = []

    async def send(m):
        sent.append(m)
    r.attach(send, {"netbird_ip": "192.0.2.20"})
    r.on_snapshot(SNAP)
    snaps = await r.snapshot()
    assert [s.session_id for s in snaps] == ["mac-A"]
    assert snaps[0].urls == ("http://localhost:5173/",) and snaps[0].url_host == "192.0.2.20"
    assert not snaps[0].offline and await r.active_session() == "mac-A" and r.projects == ["Atlas"]
    r.detach()
    assert (await r.snapshot())[0].offline and await r.active_session() is None


async def test_command_round_trip_and_errors():
    r = RemoteSource(timeout=0.5)
    sent = []

    async def send(m):
        sent.append(m)
        if m["op"] == "reply":
            r.on_result({"type": "result", "id": m["id"], "ok": True, "data": "the answer"})
        elif m["op"] == "focus":
            r.on_result({"type": "result", "id": m["id"], "ok": False, "error": "unknown tab"})
        elif m["op"] == "send_text":
            r.on_result({"type": "result", "id": m["id"], "ok": False, "error": "Claude isn't running in that tab"})
    r.attach(send, {})
    assert await r.reply_text("mac-A") == "the answer"
    with pytest.raises(KeyError):
        await r.focus("mac-A")
    with pytest.raises(AgentError, match="isn't running"):
        await r.send_text("mac-A", "x")
    assert sent[0] == {"type": "cmd", "id": 1, "op": "reply", "sid": "mac-A"}


async def test_timeout_and_offline():
    r = RemoteSource(timeout=0.1)

    async def silent(m):
        pass
    with pytest.raises(AgentOffline):
        await r.send_keys("mac-A", "\r")
    r.attach(silent, {})
    with pytest.raises(AgentTimeout):
        await r.send_keys("mac-A", "\r")


async def test_disconnect_fails_waiting_commands():
    r = RemoteSource(timeout=5)

    async def silent(m):
        pass
    r.attach(silent, {})
    waiting = asyncio.create_task(r.focus("mac-A"))
    await asyncio.sleep(0.01)
    r.detach()
    with pytest.raises(AgentOffline):
        await waiting


async def test_old_socket_closing_does_not_knock_out_the_new_one():
    r = RemoteSource(timeout=1)

    async def old(m):
        pass
    replies = []

    async def new(m):
        replies.append(m)
        r.on_result({"type": "result", "id": m["id"], "ok": True})
    r.attach(old, {})
    r.attach(new, {})  # the Mac reconnected before the hub noticed the old socket died
    r.detach(old)
    assert r.online
    await r.focus("mac-A")
    assert replies and replies[0]["op"] == "focus"
    r.detach(new)
    assert not r.online


async def test_send_failure_is_offline_and_leaves_nothing_waiting():
    r = RemoteSource(timeout=1)

    async def broken(m):
        raise RuntimeError("socket closing")
    r.attach(broken, {})
    with pytest.raises(AgentOffline):
        await r.focus("mac-A")
    assert r._waiting == {}


async def test_create_tab_gets_a_longer_timeout():
    r = RemoteSource(timeout=0.05)

    async def slow(m):
        await asyncio.sleep(0.2)
        r.on_result({"type": "result", "id": m["id"], "ok": True, "data": "mac-NEW"})
    r.attach(slow, {})
    r.create_timeout = 1
    assert await r.create_tab("Atlas", "claude") == "mac-NEW"


def test_bad_snapshot_values_do_not_raise():
    r = RemoteSource()
    r.on_snapshot({"type": "snapshot", "sessions": [{"id": "mac-A", "pid": "not-a-number"}]})


async def test_remote_flag_is_set_without_netbird_ip():
    r = RemoteSource()
    r.attach(lambda m: None, {})
    r.on_snapshot(SNAP)
    assert (await r.snapshot())[0].remote is True


async def test_servers_and_server_tabs_from_the_snapshot():
    r = RemoteSource()
    sent = []

    async def send(m):
        sent.append(m)
        r.on_result({"type": "result", "id": m["id"], "ok": True, "data": "mac-W" if m["op"] == "create_on_server" else None})
    r.attach(send, {})
    r.on_snapshot({"type": "snapshot", "active": None, "projects": [], "servers": [
        {"name": "trading", "online": True, "projects": ["edx", "../x"]}, {"name": "bad name!", "online": True}],
        "sessions": [{"id": "mac-T", "title": "edx", "cwd": "", "job": "claude", "tty": "", "pid": 0, "screen": "",
                      "server": "trading", "pane": "%4"}]})
    assert r.servers == [{"name": "trading", "online": True, "projects": ["edx"]}]
    s = (await r.snapshot())[0]
    assert (s.server, s.pane) == ("trading", "%4")
    assert await r.create_on_server("trading", "edx") == "mac-W"
    await r.focus_pane("hub1", "%3")
    assert [(m["op"], m.get("server"), m.get("project") or m.get("pane")) for m in sent] == [
        ("create_on_server", "trading", "edx"), ("focus_pane", "hub1", "%3")]
