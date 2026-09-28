import os
import time
from pathlib import Path

from tabdeck.config import Config, detect_netbird_ip, list_projects

STATUS = """OS: darwin/arm64
Daemon version: 0.36.0
Management: Connected
FQDN: mac.netbird.example
NetBird IP: 192.0.2.20/16
"""


def test_detect_netbird_ip():
    assert detect_netbird_ip(STATUS) == "192.0.2.20"


def test_detect_netbird_ip_missing():
    assert detect_netbird_ip("Daemon status: NeedsLogin") is None


def test_config_paths(tmp_path):
    c = Config(data_dir=tmp_path)
    assert c.cert_file == tmp_path / "cert.pem"
    assert c.tokens_file == tmp_path / "tokens.json"
    assert c.state_file == tmp_path / "state.json"
    assert c.port == 8765


def test_list_projects_newest_first(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "new").mkdir()
    past = time.time() - 1000
    os.utime(tmp_path / "old", (past, past))
    assert list_projects(tmp_path) == ["new", "old"]


def test_list_projects_missing_dir(tmp_path):
    assert list_projects(tmp_path / "nope") == []


def test_settings_file_overrides_defaults(tmp_path, monkeypatch):
    import json
    from tabdeck.config import load_config
    monkeypatch.delenv("TABDECK_OLLAMA_URL", raising=False)
    monkeypatch.setenv("TABDECK_NETBIRD_IP", "192.0.2.20")
    (tmp_path / "settings.json").write_text(json.dumps({"ollama_url": "http://192.0.2.10:11434",
                                                        "intent_model": "gemma4:latest"}))
    c = load_config(data_dir=tmp_path)
    assert c.ollama_url == "http://192.0.2.10:11434" and c.intent_model == "gemma4:latest"
    assert load_config(data_dir=tmp_path / "none").ollama_url == "http://localhost:11434"


def test_source_defaults_by_platform_and_settings_override(tmp_path, monkeypatch):
    import json, sys
    from tabdeck.config import load_config
    monkeypatch.setenv("TABDECK_NETBIRD_IP", "192.0.2.10")
    assert load_config(data_dir=tmp_path).source == ("iterm" if sys.platform == "darwin" else "tmux")
    (tmp_path / "settings.json").write_text(json.dumps({"source": "tmux"}))
    assert load_config(data_dir=tmp_path).source == "tmux"
