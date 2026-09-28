import pytest

pytest.importorskip("iterm2")  # macOS only (the Mac agent); CI on Linux skips these

from tabdeck.iterm_bridge import ItermBridge


class Line:
    def __init__(self, s):
        self.string = s


class Contents:
    number_of_lines = 1

    def line(self, i):
        return Line("hi")


class FakeSession:
    def __init__(self, sid, fail=False):
        self.session_id, self.name, self.fail = sid, "t", fail

    async def async_get_screen_contents(self):
        if self.fail:
            raise RuntimeError("session closed mid-read")
        return Contents()

    async def async_get_variable(self, name):
        return {"pid": 1}.get(name, "x")


class FakeApp:
    def __init__(self, live):
        self.live = live

    def get_session_by_id(self, sid):
        return self.live.get(sid)


async def test_snapshot_skips_session_that_closed_mid_read():
    b = ItermBridge()
    ok, closing = FakeSession("A"), FakeSession("B", fail=True)
    b.app = FakeApp({"A": ok})
    b._sessions = lambda: [ok, closing]
    assert [s.session_id for s in await b.snapshot()] == ["A"]


async def test_snapshot_raises_when_live_session_fails():
    import pytest
    b = ItermBridge()
    bad = FakeSession("B", fail=True)
    b.app = FakeApp({"B": bad})
    b._sessions = lambda: [bad]
    with pytest.raises(RuntimeError):
        await b.snapshot()


async def test_reset_closes_old_connection():
    closed = []

    class WS:
        async def close(self):
            closed.append(True)

    class Conn:
        websocket = WS()

    b = ItermBridge()
    b.connection, b.app = Conn(), object()
    await b.reset()
    assert closed == [True] and not b.connected


class TSession(FakeSession):
    def __init__(self, sid, pane=None, path=""):
        super().__init__(sid)
        self.pane, self.path, self.vars = pane, path, {}

    async def async_get_variable(self, name):
        if name == "tmuxWindowPane":
            return self.pane
        if name == "path":
            return self.path
        if name in self.vars:
            return self.vars[name]
        return {"pid": 1}.get(name, "x")

    async def async_set_variable(self, name, value):
        self.vars[name] = value


class Tab:
    def __init__(self, sessions, window=None):
        # iTerm leaves a tab's tmux connection id empty, so the bridge must not rely on it.
        self.sessions, self.tmux_connection_id, self.tmux_window_id = sessions, "", window


class Conn:
    def __init__(self, cid, owner, panes):
        self.connection_id = cid
        self.owning_session = FakeSession(owner)
        self.panes, self.commands = panes, []

    async def async_send_command(self, cmd):
        self.commands.append(cmd)
        return self.panes if cmd.startswith("list-panes") else "edx\n_keep"


def tmux_bridge():
    b = ItermBridge()
    mac = TSession("M")
    edx = TSession("E", pane=4, path="/home/h/Projects/edx")           # on trading
    api = TSession("H", pane="%7", path="/home/dev/Projects/api")   # on hub1
    twin = TSession("X", pane=2, path="/home/h/Projects/same")         # same ids and folder on both servers
    gateway = TSession("GW")
    b.app = FakeApp({s.session_id: s for s in (mac, edx, api, twin, gateway)})
    tabs = [Tab([mac]), Tab([edx], window="3"), Tab([api], window="9"), Tab([twin], window="2"), Tab([gateway])]
    b._tabs = lambda: tabs
    b._sessions = lambda: [s for t in tabs for s in t.sessions]
    conns = {"c1": Conn("c1", "GW", "@3 %4 /home/h/Projects/edx\n@2 %2 /home/h/Projects/same"),
             "c2": Conn("c2", "GW2", "@9 %7 /home/dev/Projects/api\n@2 %2 /home/h/Projects/same")}

    async def tmux_connections():
        return conns
    b._tmux_connections = tmux_connections
    b.gateway_servers = {"GW": "trading", "GW2": "hub1"}
    return b, conns


async def test_snapshot_tags_tmux_tabs_with_their_server_and_skips_gateways():
    b, _ = tmux_bridge()
    snaps = {s.session_id: s for s in await b.snapshot()}
    assert set(snaps) == {"M", "E", "H", "X"}
    assert (snaps["M"].server, snaps["M"].pane) == ("", "")
    assert (snaps["E"].server, snaps["E"].pane) == ("trading", "%4")
    assert (snaps["H"].server, snaps["H"].pane) == ("hub1", "%7")
    assert snaps["X"].server == ""  # ambiguous: left untagged rather than guessed


async def test_tmux_commands_and_window_lookup_go_through_the_gateway():
    b, conns = tmux_bridge()
    assert await b.tmux_command("GW", "list-windows") == "edx\n_keep"
    assert conns["c1"].commands == ["list-windows"]
    assert await b.session_for_window("GW", "@3") == "E"
    assert await b.session_for_window("GW", "@9") is None  # that window is on the other server
    assert await b.live_owners() == {"GW", "GW2"}
    n = len(conns["c1"].commands)
    await b.snapshot()
    await b.snapshot()
    assert len(conns["c1"].commands) == n + 2  # matched tabs are remembered; only the ambiguous one is retried
