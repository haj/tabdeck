import subprocess
from pathlib import Path

from tabdeck.install import HOOK_EVENTS, build_plist, merge_hooks, remove_hooks

HOOK = Path(__file__).parent.parent / "hooks" / "tabdeck-hook.sh"
CMD = "/Users/x/.tabdeck/hook.sh"


def existing():
    return {"model": "opus", "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "remember.sh"}]}],
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt.sh"}]}],
    }}


def test_merge_preserves_existing_and_is_idempotent():
    s = merge_hooks(existing(), CMD)
    s = merge_hooks(s, CMD)
    assert s["model"] == "opus"
    assert s["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "remember.sh"
    assert s["hooks"]["PostToolUse"][0]["matcher"] == "Edit"
    for ev in HOOK_EVENTS:
        cmds = [h["command"] for g in s["hooks"][ev] for h in g["hooks"]]
        assert cmds.count(CMD) == 1


def test_remove_restores_original():
    assert remove_hooks(merge_hooks(existing(), CMD), CMD) == existing()


def test_plist():
    p = build_plist("/Users/x/.local/bin/uv", Path("/Users/x/Projects/TabDeck"), Path("/Users/x/.tabdeck/service.log"))
    assert p["Label"] == "com.tabdeck.service"
    assert p["ProgramArguments"] == ["/Users/x/.local/bin/uv", "run", "--project",
                                     "/Users/x/Projects/TabDeck", "tabdeck", "serve"]
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"]


def test_hook_script_is_silent_and_succeeds_when_service_down():
    r = subprocess.run(["sh", str(HOOK)], input=b'{"hook_event_name":"Stop"}', capture_output=True,
                       env={"ITERM_SESSION_ID": "w0t0p0:X", "TABDECK_PORT": "1", "PATH": "/usr/bin:/bin"},
                       timeout=5)
    assert r.returncode == 0 and r.stdout == b""


def test_hook_script_outside_iterm():
    r = subprocess.run(["sh", str(HOOK)], input=b"{}", capture_output=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout == b""


def test_widget_plist():
    from tabdeck.install import build_widget_plist
    p = build_widget_plist(Path("/Users/x/Applications/DeckWidget.app"), Path("/Users/x/.tabdeck/widget.log"))
    assert p["Label"] == "com.tabdeck.widget"
    assert p["ProgramArguments"] == ["/Users/x/Applications/DeckWidget.app/Contents/MacOS/DeckWidget"]
    assert p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["StandardErrorPath"] == "/Users/x/.tabdeck/widget.log"


def test_hook_script_sends_tmux_pane_header(tmp_path):
    fake_curl = tmp_path / "curl"
    fake_curl.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "$OUT"\ncat >/dev/null\n')
    fake_curl.chmod(0o755)
    out = tmp_path / "args"
    r = subprocess.run(["sh", str(HOOK)], input=b"{}", capture_output=True,
                       env={"TMUX_PANE": "%3", "PATH": f"{tmp_path}:/usr/bin:/bin", "OUT": str(out)})
    assert r.returncode == 0 and r.stdout == b""
    assert "X-Tmux-Pane: %3" in out.read_text()


def test_tmux_runs_in_its_own_service_not_the_hubs():
    setup = (Path(__file__).parent.parent / "scripts" / "hub-setup.sh").read_text()
    assert "tabdeck$SUFFIX-tmux.service" in setup and "Requires=tabdeck$SUFFIX-tmux.service" in setup
    assert "ExecStart=$D/deck-start.sh" in setup and "deck-save.sh" in setup


def test_deploy_does_not_copy_private_notes():
    deploy = (Path(__file__).parent.parent / "scripts" / "deploy-hub.sh").read_text()
    assert "--exclude .remember" in deploy


def test_plist_can_run_the_agent():
    p = build_plist("/u/uv", Path("/r"), Path("/l.log"), command="agent")
    assert p["ProgramArguments"][-1] == "agent"
    assert build_plist("/u/uv", Path("/r"), Path("/l.log"))["ProgramArguments"][-1] == "serve"


def test_crontab_merge_is_idempotent_and_keeps_the_users_lines():
    from tabdeck.install import merge_crontab
    mine = "MAILTO=me\n0 3 * * * backup.sh\n"
    once = merge_crontab(mine, reboot=True)
    assert once.startswith(mine)
    assert "@reboot $HOME/.tabdeck/deck-start.sh # tabdeck" in once
    assert "* * * * * $HOME/.tabdeck/deck-save.sh # tabdeck" in once
    assert merge_crontab(once, reboot=True) == once
    assert "@reboot" not in merge_crontab("", reboot=False)


def test_servers_file_gains_or_replaces_an_entry(tmp_path):
    import json
    from tabdeck.install import upsert_server
    p = tmp_path / "servers.json"
    upsert_server(p, {"name": "trading", "ssh": "a@192.0.2.60", "host": "192.0.2.60", "projects": "Projects"})
    upsert_server(p, {"name": "gpubox", "ssh": "gpubox", "host": "192.0.2.30", "projects": "Projects"})
    upsert_server(p, {"name": "trading", "ssh": "b@192.0.2.60", "host": "192.0.2.60", "projects": "Orbit"})
    data = json.loads(p.read_text())
    assert [(e["name"], e["ssh"], e["projects"]) for e in data] == [("trading", "b@192.0.2.60", "Orbit"),
                                                                    ("gpubox", "gpubox", "Projects")]


def test_install_server_refuses_bad_targets(tmp_path):
    import pytest
    from tabdeck.config import Config
    from tabdeck.install import install_server
    for ssh, name in (("-oProxyCommand=x", "a"), ("a@b", "bad name"), ("a@b;x", "a")):
        with pytest.raises(SystemExit):
            install_server(Config(data_dir=tmp_path), ssh, name)


def test_deploy_env_file_can_be_chosen(tmp_path, monkeypatch):
    from tabdeck.install import deploy_env
    (tmp_path / "deploy.env").write_text("HUB_HOST=a@one\n")
    (tmp_path / "deploy-two.env").write_text("HUB_HOST=b@two\nHUB_IP='100.64.0.2'\n")
    monkeypatch.delenv("DEPLOY_ENV", raising=False)
    assert deploy_env(tmp_path / "deploy.env")["HUB_HOST"] == "a@one"
    monkeypatch.setenv("DEPLOY_ENV", "deploy-two.env")
    assert deploy_env(tmp_path / "deploy.env") == {"HUB_HOST": "b@two", "HUB_IP": "100.64.0.2"}
