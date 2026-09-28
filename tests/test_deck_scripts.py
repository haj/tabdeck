import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TMUX = shutil.which("tmux") or ("/opt/homebrew/bin/tmux" if Path("/opt/homebrew/bin/tmux").exists() else None)
pytestmark = pytest.mark.skipif(TMUX is None, reason="tmux not installed")


@pytest.fixture
def box(tmp_path):
    """A private HOME and tmux server, with a fake `claude` that logs its arguments and stays running."""
    home = tmp_path / "home"
    (home / ".tabdeck").mkdir(parents=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text('#!/bin/sh\necho "args:$*" >> "$HOME/claude.log"\nexec sleep 300\n')
    (bin_ / "claude").chmod(0o755)
    sock = Path(tempfile.mkdtemp(prefix="dk", dir="/tmp"))  # unix socket paths must stay short
    env = {"HOME": str(home), "TMUX_TMPDIR": str(sock), "PATH": f"{bin_}:{Path(TMUX).parent}:/usr/bin:/bin"}
    yield home, env
    subprocess.run([TMUX, "kill-server"], env=env, capture_output=True)
    shutil.rmtree(sock, ignore_errors=True)


def run(env, script):
    return subprocess.run(["sh", str(REPO / "scripts" / script)], env=env, capture_output=True, text=True)


def tmux(env, *args):
    return subprocess.run([TMUX, *args], env=env, capture_output=True, text=True).stdout


def windows(env):
    return tmux(env, "list-windows", "-t", "=deck", "-F", "#{window_name}").split()


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_start_creates_deck_with_a_keepalive_window(box):
    _, env = box
    assert run(env, "deck-start.sh").returncode == 0
    assert windows(env) == ["_keep"]


def test_start_restores_listed_windows_and_continues_known_conversations(box, tmp_path):
    home, env = box
    edx, api = tmp_path / "Projects" / "edx", tmp_path / "Projects" / "api"
    edx.mkdir(parents=True)
    api.mkdir()
    conv = home / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in str(edx))
    conv.mkdir(parents=True)
    (conv / "abc.jsonl").write_text("{}\n")
    (home / ".tabdeck" / "deck.tsv").write_text(f"edx\t{edx}\napi\t{api}\ngone\t{tmp_path}/missing\n")
    run(env, "deck-start.sh")
    assert windows(env) == ["_keep", "edx", "api"]
    log = home / "claude.log"
    assert wait_for(lambda: log.exists() and len(log.read_text().splitlines()) == 2)
    assert sorted(log.read_text().splitlines()) == ["args:", "args:--continue"]
    run(env, "deck-start.sh")  # again: nothing is duplicated
    assert windows(env) == ["_keep", "edx", "api"]


def test_save_lists_only_claude_windows(box, tmp_path):
    home, env = box
    if shutil.which("cc") is None:
        pytest.skip("no C compiler for the fake claude binary")
    sleeper = tmp_path / "real" / "claude"
    sleeper.parent.mkdir()
    (tmp_path / "real" / "c.c").write_text("#include <unistd.h>\nint main(void){for(;;)pause();}\n")
    # A real binary named claude (a script would show as "sh" in tmux).
    subprocess.run(["cc", "-o", str(sleeper), str(tmp_path / "real" / "c.c")], check=True)
    run(env, "deck-start.sh")
    tmux(env, "new-window", "-d", "-t", "=deck:", "-n", "edx", "-c", str(tmp_path), str(sleeper))
    tmux(env, "new-window", "-d", "-t", "=deck:", "-n", "shell", "-c", str(tmp_path), "sleep 300")
    lst = home / ".tabdeck" / "deck.tsv"
    assert wait_for(lambda: run(env, "deck-save.sh") and lst.exists() and "edx" in lst.read_text())
    assert [l.split("\t")[0] for l in lst.read_text().splitlines()] == ["edx"]
    assert lst.read_text().splitlines()[0].split("\t")[1] == str(tmp_path.resolve())
    # A listed window stays while open (Claude may be running a tool); a closed one drops out.
    lst.write_text(f"shell\t{tmp_path}\nclosed\t{tmp_path}\n")
    run(env, "deck-save.sh")
    assert sorted(l.split("\t")[0] for l in lst.read_text().splitlines()) == ["edx", "shell"]


def test_save_without_tmux_keeps_the_list(box):
    home, env = box
    lst = home / ".tabdeck" / "deck.tsv"
    lst.write_text("edx\t/p/edx\n")
    assert run(env, "deck-save.sh").returncode == 0
    assert lst.read_text() == "edx\t/p/edx\n"


def test_save_keeps_window_names_and_folders_with_spaces(box, tmp_path):
    home, env = box
    spaced = tmp_path / "my project"
    spaced.mkdir()
    run(env, "deck-start.sh")
    tmux(env, "new-window", "-d", "-t", "=deck:", "-n", "edx fix", "-c", str(spaced), "sleep 300")
    lst = home / ".tabdeck" / "deck.tsv"
    lst.write_text(f"edx fix\t{spaced}\n")  # listed earlier, still open
    run(env, "deck-save.sh")
    assert lst.read_text() == f"edx fix\t{spaced.resolve()}\n"


@pytest.fixture
def ods_box(tmp_path):
    """An "ods" instance running OpenCode: ~/.tabdeck-ods with instance.env and agent.sh, and a fake opencode."""
    home = tmp_path / "home"
    d = home / ".tabdeck-ods"
    (d / "opencode").mkdir(parents=True)
    (d / "instance.env").write_text("TABDECK_SESSION=ods\nTABDECK_AGENT=opencode\n")
    shutil.copy(REPO / "scripts" / "agent.sh", d / "agent.sh")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "opencode").write_text('#!/bin/sh\necho "args:$* config:$OPENCODE_CONFIG_DIR" >> "$HOME/agent.log"\nexec sleep 300\n')
    (bin_ / "opencode").chmod(0o755)
    sock = Path(tempfile.mkdtemp(prefix="dk", dir="/tmp"))
    env = {"HOME": str(home), "TMUX_TMPDIR": str(sock), "TABDECK_INSTANCE": "ods",
           "PATH": f"{bin_}:{Path(TMUX).parent}:/usr/bin:/bin"}
    yield home, env
    subprocess.run([TMUX, "kill-server"], env=env, capture_output=True)
    shutil.rmtree(sock, ignore_errors=True)


def test_opencode_instance_restores_its_own_session_through_agent_sh(ods_box, tmp_path):
    home, env = ods_box
    edx = tmp_path / "Projects" / "edx"
    edx.mkdir(parents=True)
    (home / ".tabdeck-ods" / "ods.tsv").write_text(f"edx\t{edx}\n")
    run(env, "deck-start.sh")
    assert tmux(env, "list-windows", "-t", "=ods", "-F", "#{window_name}").split() == ["_keep", "edx"]
    assert tmux(env, "has-session", "-t", "=deck") == ""  # never touches another instance's session
    log = home / "agent.log"
    assert wait_for(lambda: log.exists() and log.read_text().strip())
    assert log.read_text().strip() == f"args:--continue config:{home}/.tabdeck-ods/opencode"


def test_opencode_instance_saves_opencode_windows(ods_box, tmp_path):
    home, env = ods_box
    if shutil.which("cc") is None:
        pytest.skip("no C compiler for the fake opencode binary")
    real = tmp_path / "real" / "opencode"
    real.parent.mkdir()
    (tmp_path / "real" / "c.c").write_text("#include <unistd.h>\nint main(void){for(;;)pause();}\n")
    subprocess.run(["cc", "-o", str(real), str(tmp_path / "real" / "c.c")], check=True)
    run(env, "deck-start.sh")
    tmux(env, "new-window", "-d", "-t", "=ods:", "-n", "edx", "-c", str(tmp_path), str(real))
    tmux(env, "new-window", "-d", "-t", "=ods:", "-n", "shell", "-c", str(tmp_path), "sleep 300")
    lst = home / ".tabdeck-ods" / "ods.tsv"
    assert wait_for(lambda: run(env, "deck-save.sh") and lst.exists() and "edx" in lst.read_text())
    assert [l.split("\t")[0] for l in lst.read_text().splitlines()] == ["edx"]
