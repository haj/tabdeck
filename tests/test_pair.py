import pytest

from tabdeck.config import Config
from tabdeck.pair import request_pairing


def test_pairing_link_comes_from_the_hub_with_the_agent_token(tmp_path):
    seen = {}

    def post(url, token, cafile):
        seen.update(url=url, token=token, cafile=cafile)
        return {"url": "https://100.64.0.10:8765/pair?code=abc"}
    config = Config(data_dir=tmp_path, settings={"hub_url": "https://100.64.0.10:8765/", "agent_token": "t",
                                                 "hub_ca": "/ca.pem"})
    assert request_pairing(config, post=post) == "https://100.64.0.10:8765/pair?code=abc"
    assert seen == {"url": "https://100.64.0.10:8765/api/pair", "token": "t", "cafile": "/ca.pem"}


def test_pairing_needs_a_connected_mac_agent(tmp_path):
    with pytest.raises(SystemExit, match="install-agent|setup"):
        request_pairing(Config(data_dir=tmp_path, settings={}), post=lambda *a: {})


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "-a Calculator", "tabdeck://x", "https://evil.example/pair?code=a",
                                 "http://100.64.0.10:8765/pair?code=a"])
def test_only_a_pairing_link_on_the_hub_itself_is_accepted(tmp_path, bad):
    config = Config(data_dir=tmp_path, settings={"hub_url": "https://100.64.0.10:8765/", "agent_token": "t"})
    with pytest.raises(SystemExit, match="unexpected pairing link"):
        request_pairing(config, post=lambda *a: {"url": bad})


def test_tabdeck_open_asks_the_local_hub_for_a_session_in_that_folder(tmp_path, monkeypatch):
    from tabdeck.pair import open_folder
    (tmp_path / "api").mkdir()
    monkeypatch.chdir(tmp_path)
    seen = {}

    def post(url, body, verify):
        seen.update(url=url, body=body, verify=verify)
        return {"id": "tmux-4"}
    config = Config(data_dir=tmp_path / "data", port=8766)
    assert open_folder(config, "api", task="run the tests", post=post) == "tmux-4"
    assert seen["url"] == "https://127.0.0.1:8766/api/new_session"
    assert seen["body"] == {"project": "", "path": str((tmp_path / "api").resolve()), "task": "run the tests"}
