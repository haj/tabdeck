import json

from tabdeck.servers import Server, load_servers


def test_load_servers_validates_and_fills_defaults(tmp_path, caplog):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps([
        {"name": "trading", "ssh": "dev@192.0.2.40"},
        {"name": "gpubox", "ssh": "gpubox", "host": "192.0.2.30", "projects": "Orbit"},
        {"name": "bad name", "ssh": "a@b"},
        {"name": "evil", "ssh": "-oProxyCommand=x"},
        {"name": "evil2", "ssh": "a@b;rm"},
        "junk",
    ]))
    assert load_servers(p) == [Server("trading", "dev@192.0.2.40", "192.0.2.40", "Projects"),
                               Server("gpubox", "gpubox", "192.0.2.30", "Orbit")]


def test_missing_or_broken_file_means_no_servers(tmp_path):
    assert load_servers(tmp_path / "nope.json") == []
    (tmp_path / "bad.json").write_text("{")
    assert load_servers(tmp_path / "bad.json") == []
