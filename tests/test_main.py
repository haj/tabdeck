from tabdeck.config import Config
from tabdeck.main import refresh_urls
from tabdeck.registry import Registry, Snapshot


class FakeForwarders:
    def __init__(self):
        self.ports = None

    async def ensure(self, ports):
        self.ports = ports


async def test_refresh_urls_sets_urls_and_forwards(tmp_path):
    r = Registry()
    r.update([Snapshot("A", "t", "/p/Atlas", "npm", "/dev/ttys1", 100, "http://localhost:5173/app")], 1)
    ps = "100 1\n200 100\n"
    lsof = "p200\nf3\nn127.0.0.1:5173\np300\nf4\nn*:8000\np400\nf5\nn127.0.0.1:8765\n"
    fw = FakeForwarders()
    await refresh_urls(r, fw, Config(data_dir=tmp_path, netbird_ip="192.0.2.20"), ps, lsof)
    assert r.sessions["A"].urls == ["http://localhost:5173/app"]
    assert fw.ports == {5173}


async def test_own_forwarder_listener_does_not_cancel_forwarding(tmp_path):
    import os
    r = Registry()
    r.update([Snapshot("A", "t", "/p/Atlas", "npm", "/dev/ttys1", 100, "")], 1)
    lsof = f"p200\nf3\nn127.0.0.1:5173\np{os.getpid()}\nf9\nn192.0.2.20:5173\n"
    fw = FakeForwarders()
    await refresh_urls(r, fw, Config(data_dir=tmp_path, netbird_ip="192.0.2.20"), "100 1\n200 100\n", lsof)
    assert fw.ports == {5173}


async def test_only_ports_owned_by_a_tab_are_forwarded(tmp_path):
    r = Registry()
    r.update([Snapshot("A", "t", "/p/Atlas", "npm", "/dev/ttys1", 100, "see http://localhost:11434/")], 1)
    r.pin_url("A", "http://localhost:9222/")
    lsof = "p900\nf3\nn127.0.0.1:11434\np901\nf4\nn127.0.0.1:9222\n"
    fw = FakeForwarders()
    await refresh_urls(r, fw, Config(data_dir=tmp_path, netbird_ip="192.0.2.20"), "100 1\n", lsof)
    assert "http://localhost:9222/" in r.sessions["A"].urls
    assert fw.ports == set()


async def test_poll_once_records_active_tab():
    from tabdeck.main import poll_once

    class Bridge:
        connected = True

        async def snapshot(self):
            return [Snapshot("A", "t", "/p/x", "zsh", "/dev/ttys1", 1, ""), Snapshot("B", "t", "/p/y", "zsh", "/dev/ttys2", 2, "")]

        async def active_session(self):
            return "B"

    r = Registry()
    await poll_once(r, Bridge())
    assert set(r.sessions) == {"A", "B"} and r.active == "B" and r.iterm_ok


def test_make_source_picks_tmux(tmp_path):
    from tabdeck.main import make_source
    from tabdeck.tmux_source import TmuxSource
    src = make_source(Config(data_dir=tmp_path, source="tmux"))
    assert isinstance(src.local, TmuxSource) and "mac" in src.remotes


async def test_refresh_urls_leaves_remote_tabs_alone(tmp_path):
    r = Registry()
    r.update([Snapshot("mac-A", "t", "/p/F", "claude", "", 100, "", urls=("http://localhost:9999/",),
                       url_host="192.0.2.20", remote=True)], 1)
    await refresh_urls(r, FakeForwarders(), Config(data_dir=tmp_path, netbird_ip="192.0.2.10"), "100 1\n", "")
    assert r.sessions["mac-A"].urls == ["http://localhost:9999/"]


async def test_refresh_urls_skips_remote_tabs_even_without_url_host(tmp_path):
    r = Registry()
    r.update([Snapshot("mac-A", "t", "/p/F", "claude", "", 100, "", urls=("http://localhost:9999/",), remote=True)], 1)
    await refresh_urls(r, FakeForwarders(), Config(data_dir=tmp_path, netbird_ip="192.0.2.10"), "100 1\n", "")
    assert r.sessions["mac-A"].urls == ["http://localhost:9999/"]


def test_the_hub_only_listens_on_private_addresses():
    from tabdeck.main import private_address
    for ip in ("100.103.1.2", "10.0.0.5", "192.168.1.9", "172.16.3.4", "127.0.0.1", "100.64.0.1"):
        assert private_address(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "0.0.0.0", "", "not-an-ip"):
        assert not private_address(ip), ip


def test_allow_public_is_an_explicit_setting(tmp_path):
    import json
    from tabdeck.config import load_config
    assert load_config(tmp_path).allow_public is False
    (tmp_path / "settings.json").write_text(json.dumps({"allow_public": True}))
    assert load_config(tmp_path).allow_public is True
