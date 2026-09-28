import json
from types import SimpleNamespace

import pytest

from tabdeck.setup import Answers, detect_hub, plan_files, validate, write_files


def answers(**kw):
    base = dict(instance="", host="me@hub", ip="100.64.0.10", agent="claude", assistant="Jarvis", wake="jarvis",
                port=8765, session="deck", ods=False, hub_name="hub", mac_tabs=True, fqdn="")
    base.update(kw)
    return Answers(**base)


def test_validate_accepts_good_answers_and_names_every_bad_one():
    assert validate(answers()) == []
    bad = answers(host="-oProxyCommand=x", ip="999.1.1.1", agent="vim", instance="Bad Name", port=80,
                  wake="", session="a b", hub_name="../x")
    problems = " ".join(validate(bad))
    for word in ("ssh target", "IP", "agent", "instance", "port", "wake word", "tmux session", "hub name"):
        assert word in problems


def test_plan_for_a_claude_hub():
    files = plan_files(answers(), repo="/r", data_dir="/home/me/.tabdeck")
    env = files["/r/deploy.env"]
    assert "HUB_HOST=me@hub\n" in env and "HUB_IP=100.64.0.10\n" in env and "HUB_AGENT=claude\n" in env
    assert "TABDECK_INSTANCE" not in env and "HUB_ODS=1" not in env
    settings = json.loads(files["/home/me/.tabdeck/settings.json"])
    assert settings == {"agent": "claude", "assistant_name": "Jarvis", "wake_word": "jarvis", "port": 8765,
                        "tmux_session": "deck", "hub_server": "hub", "mac_tabs": True}
    servers = json.loads(files["/home/me/.tabdeck/servers.json"])
    assert servers == [{"name": "hub", "ssh": "me@hub", "host": "100.64.0.10", "projects": "Projects"}]


def test_plan_for_a_second_instance_on_ods():
    a = answers(instance="ods", agent="opencode", assistant="ODS", wake="hey ods", port=8766, session="ods",
                ods=True, hub_name="gpubox", mac_tabs=False)
    files = plan_files(a, repo="/r", data_dir="/home/me/.tabdeck-ods")
    env = files["/r/deploy-ods.env"]  # one deploy file per setup
    for line in ("TABDECK_INSTANCE=ods", "HUB_AGENT=opencode", "HUB_ODS=1", "HUB_PORT=8766", "HUB_SESSION=ods",
                 "HUB_ASSISTANT=ODS", "HUB_WAKE='hey ods'"):
        assert line + "\n" in env
    settings = json.loads(files["/home/me/.tabdeck-ods/settings.json"])
    assert settings["mac_tabs"] is False and settings["wake_word"] == "hey ods"


def test_existing_settings_and_servers_are_kept(tmp_path):
    d = tmp_path / ".tabdeck"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"agent_token": "secret", "voice": "Daniel", "agent": "opencode"}))
    (d / "servers.json").write_text(json.dumps([{"name": "other", "ssh": "o@o", "host": "100.64.0.9", "projects": "P"}]))
    files = plan_files(answers(), repo=str(tmp_path), data_dir=str(d))
    s = json.loads(files[f"{d}/settings.json"])
    assert s["agent_token"] == "secret" and s["voice"] == "Daniel" and s["agent"] == "claude"
    assert [x["name"] for x in json.loads(files[f"{d}/servers.json"])] == ["other", "hub"]
    write_files(files)
    assert (d / "settings.json").stat().st_mode & 0o777 == 0o600  # it holds the agent token
    assert (tmp_path / "deploy.env").stat().st_mode & 0o777 == 0o600


def test_detect_hub_reads_hostname_vpn_ip_and_ods_over_ssh():
    def run(args, **kw):
        cmd = args[-1]
        if "hostname" in cmd:
            return SimpleNamespace(returncode=0, stdout="GpuBox\n")
        if "netbird status" in cmd:
            return SimpleNamespace(returncode=0, stdout="Management: Connected\nNetBird IP: 100.64.3.4/16\n")
        if "ods/.env" in cmd:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout="")
    assert detect_hub("me@gpubox", run=run) == {"hub_name": "gpubox", "ip": "100.64.3.4", "ods": True}

    def down(args, **kw):
        return SimpleNamespace(returncode=255, stdout="")
    with pytest.raises(SystemExit):
        detect_hub("me@gone", run=down)


def test_questions_suggest_ods_defaults_when_ods_is_found():
    from tabdeck.setup import run_setup

    def run(args, **kw):
        cmd = args[-1]
        out = {"hostname -s || hostname": "gpubox\n", "netbird status 2>/dev/null": "NetBird IP: 100.64.3.4/16\n"}
        return SimpleNamespace(returncode=0, stdout=out.get(cmd, ""))
    replies = iter(["ods", "me@gpubox", "", "", "", "", "", ""])  # instance, host, then Enter keeps each suggestion
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return next(replies)
    a = run_setup({}, ask=ask, run=run, say=lambda *x: None)
    assert (a.instance, a.ip, a.ods, a.agent, a.assistant, a.wake, a.port, a.session, a.hub_name, a.mac_tabs) == (
        "ods", "100.64.3.4", True, "opencode", "ODS", "hey ods", 8766, "ods", "gpubox", False)
    assert any("[100.64.3.4]" in p for p in asked)  # the detected IP is offered, not typed


def test_flags_skip_questions_and_bad_flags_stop_setup():
    from tabdeck.setup import run_setup

    def run(args, **kw):
        return SimpleNamespace(returncode=0, stdout="hub\n" if "hostname" in args[-1] else "")
    opts = {"instance": "", "host": "me@hub", "ip": "100.64.0.10", "ods": False, "agent": "claude",
            "assistant": "Jarvis", "wake": "jarvis", "port": 8765}
    a = run_setup(opts, ask=lambda p: pytest.fail(f"asked {p}"), run=run, say=lambda *x: None)
    assert a.host == "me@hub" and a.session == "deck" and a.mac_tabs is True
    with pytest.raises(SystemExit, match="IP"):
        run_setup({**opts, "ip": "not-an-ip"}, ask=lambda p: "", run=run, say=lambda *x: None)


def test_detection_never_reads_the_users_input():
    import subprocess as sp
    seen = []

    def run(args, **kw):
        seen.append(kw.get("stdin"))
        return SimpleNamespace(returncode=0, stdout="hub\n")
    detect_hub("me@hub", run=run)
    assert seen and all(s is sp.DEVNULL for s in seen)  # ssh would otherwise swallow typed-ahead answers


def test_running_out_of_answers_stops_cleanly():
    from tabdeck.setup import run_setup

    def eof(prompt):
        raise EOFError
    with pytest.raises(SystemExit, match="setup needs"):
        run_setup({}, ask=eof, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=""), say=lambda *x: None)


def test_setup_refuses_a_public_hub_ip():
    problems = validate(answers(ip="8.8.8.8"))
    assert problems and "public" in problems[0]
