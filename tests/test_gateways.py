from tabdeck.gateways import GatewayManager, gateway_command
from tabdeck.servers import Server

TRADING = Server("trading", "dev@192.0.2.70", "192.0.2.70", "Projects")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Bridge:
    def __init__(self):
        self.gateway_servers, self.live, self.open, self.opened, self.closed, self.hidden = {}, set(), set(), [], [], []
        self.names = {}

    async def open_gateway(self, command, name):
        sid = f"G{len(self.opened) + 1}"
        self.opened.append(command)
        self.open.add(sid)
        self.names[sid] = name
        return sid

    async def gateway_name(self, sid):
        return self.names.get(sid)

    async def live_owners(self):
        return set(self.live)

    def alive(self, sid):
        return sid in self.open

    async def close_session(self, sid):
        self.closed.append(sid)
        self.open.discard(sid)

    async def tmux_command(self, owner, cmd):
        return "@0 _keep\n@1 edx"

    async def hide_window(self, owner, wid):
        self.hidden.append((owner, wid))


def manager(reachable=True):
    bridge, clock, state = Bridge(), Clock(), {"reachable": reachable, "lists": 0}

    async def probe(server):
        return state["reachable"]

    async def lister(server):
        state["lists"] += 1
        return "/home/dev/Projects", ["edx", "api"]
    changes = []
    m = GatewayManager([TRADING], bridge, probe=probe, lister=lister, clock=clock, on_change=lambda: changes.append(1))
    return m, bridge, clock, state, changes


def test_gateway_command_attaches_to_deck_over_ssh():
    cmd = gateway_command(TRADING)
    assert cmd.startswith("exec ssh -t dev@192.0.2.70 ") and "tmux -CC new -A -s deck" in cmd
    assert "has-session -t =deck 2>/dev/null ||" in cmd  # an existing deck is left as it is


async def test_opens_once_then_goes_live_and_hides_keepalive():
    m, bridge, clock, state, changes = manager()
    await m.tick()
    await m.tick()
    assert bridge.opened == [gateway_command(TRADING)] and bridge.gateway_servers == {"G1": "trading"}
    assert m.info("trading").online is False
    bridge.live.add("G1")
    await m.tick()
    info = m.info("trading")
    assert info.online and info.owner == "G1" and info.projects == ["edx", "api"] and info.root == "/home/dev/Projects"
    assert bridge.hidden == [("G1", "@0")] and changes
    assert m.summary() == [{"name": "trading", "online": True, "projects": ["edx", "api"]}]


async def test_a_gateway_that_never_attaches_is_closed_and_retried_with_backoff():
    m, bridge, clock, state, _ = manager()
    await m.tick()                      # opens G1
    clock.t += 31
    await m.tick()                      # no tmux after 30 s: failed, closed; next try in 15 s
    assert bridge.closed == ["G1"] and bridge.gateway_servers == {}
    await m.tick()
    assert len(bridge.opened) == 1      # still backing off
    waits = []
    for _ in range(7):
        start = clock.t
        while len(bridge.opened) == len(waits) + 1:
            clock.t += 1
            await m.tick()
        waits.append(round(clock.t - start))
        clock.t += 31
        await m.tick()                  # this one fails too
    assert waits[:5] == [15, 30, 60, 120, 240] and max(waits) <= 601


async def test_a_dropped_live_gateway_is_reopened_when_reachable():
    m, bridge, clock, state, _ = manager()
    await m.tick()
    bridge.live.add("G1")
    await m.tick()
    clock.t += 120                      # live long enough: failures forgotten
    await m.tick()
    bridge.live.clear()
    bridge.open.discard("G1")           # network dropped: iTerm closed the gateway
    state["reachable"] = False
    await m.tick()
    assert m.info("trading").online is False and len(bridge.opened) == 1
    state["reachable"] = True
    await m.tick()
    assert len(bridge.opened) == 2


async def test_unreachable_opens_nothing_and_projects_refresh_every_ten_minutes():
    m, bridge, clock, state, _ = manager(reachable=False)
    await m.tick()
    assert bridge.opened == [] and m.info("trading").online is False and m.info("nowhere") is None
    state["reachable"] = True
    await m.tick()
    bridge.live.add("G1")
    await m.tick()
    assert state["lists"] == 1
    clock.t += 300
    await m.tick()
    assert state["lists"] == 1
    clock.t += 301
    await m.tick()
    assert state["lists"] == 2


async def test_a_restarted_agent_reuses_the_open_gateway():
    m, bridge, clock, state, _ = manager()
    bridge.live.add("OLD")
    bridge.open.add("OLD")
    bridge.names["OLD"] = "trading"   # opened by the previous agent run
    await m.tick()
    assert bridge.opened == [] and m.info("trading").owner == "OLD" and m.info("trading").online
    assert bridge.gateway_servers == {"OLD": "trading"}
