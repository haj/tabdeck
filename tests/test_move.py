import json
import os
import time
from types import SimpleNamespace

import pytest

from tabdeck.move import claude_dir_name, move_session, newest_session, rewrite_cwd, unique_window
from tabdeck.servers import Server


def test_claude_dir_name_replaces_every_other_character():
    assert claude_dir_name("/Users/h/Projects/Orbit") == "-Users-h-Projects-Orbit"
    assert claude_dir_name("/home/h/my app.v2") == "-home-h-my-app-v2"


def test_newest_session_and_rewrite(tmp_path):
    (tmp_path / "old.jsonl").write_text("{}")
    (tmp_path / "new.jsonl").write_text("{}")
    os.utime(tmp_path / "old.jsonl", (1, 1))
    assert newest_session(tmp_path) == "new"
    assert newest_session(tmp_path / "missing") is None
    line = json.dumps({"cwd": "/Users/h/Projects/Orbit/sub", "text": "see /Users/h/Projects/Orbit"})
    out = json.loads(rewrite_cwd(line + "\n", "/Users/h/Projects/Orbit", "/home/h/Projects/Orbit"))
    assert out["cwd"] == "/home/h/Projects/Orbit/sub"
    assert out["text"] == "see /Users/h/Projects/Orbit"  # only the working folder field changes


def test_unique_window():
    assert unique_window("edx", {"_keep"}) == "edx"
    assert unique_window("edx", {"edx", "edx-2"}) == "edx-3"


class Runner:
    """Records commands; answers the few whose output matters."""

    def __init__(self, windows="_keep"):
        self.calls, self.windows = [], windows

    def __call__(self, args, input=None):
        self.calls.append((args, input))
        cmd = args[-1] if args[0] == "ssh" else ""
        if cmd == "echo $HOME":
            return SimpleNamespace(returncode=0, stdout="/home/dev\n", stderr="")
        if "list-windows" in cmd:
            return SimpleNamespace(returncode=0, stdout=self.windows + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def mac(tmp_path):
    projects = tmp_path / "Projects"
    (projects / "Orbit").mkdir(parents=True)
    claude = tmp_path / "claude-projects"
    hist = claude / claude_dir_name(str(projects / "Orbit"))
    hist.mkdir(parents=True)
    (hist / "abc.jsonl").write_text(json.dumps({"cwd": str(projects / "Orbit")}) + "\n")
    (hist / "memory").mkdir()
    os.utime(hist / "abc.jsonl", (time.time() - 600, time.time() - 600))
    return projects, claude, hist


SERVER = Server("hub1", "dev@192.0.2.10", "192.0.2.10", "Projects")


def test_move_copies_files_history_and_resumes_in_deck(mac, tmp_path):
    projects, claude, hist = mac
    run = Runner(windows="_keep\nOrbit")
    result = move_session("Orbit", SERVER, projects_dir=projects, claude_projects=claude, run=run)
    assert result == {"server": "hub1", "window": "Orbit-2", "session": "abc",
                      "path": "/home/dev/Projects/Orbit", "active": False}
    cmds = [c[0] for c in run.calls]
    sync = next(c for c in cmds if c[0] == "rsync" and c[-1].endswith("Projects/Orbit/"))
    assert "--exclude" in sync and "node_modules" in sync and sync[-2] == f"{projects / 'Orbit'}/"
    uploads = [c for c in run.calls if c[0][0] == "ssh" and c[1] is not None]
    assert len(uploads) == 1 and '"cwd":"/home/dev/Projects/Orbit"' in uploads[0][1]
    assert "-home-dev-Projects-Orbit/abc.jsonl" in uploads[0][0][-1]
    start = cmds[-1][-1]
    assert "tmux new-window -d -t =deck: -n Orbit-2" in start and "claude --resume abc" in start


def test_move_refuses_bad_input_and_missing_history(mac, tmp_path):
    projects, claude, hist = mac
    with pytest.raises(SystemExit):
        move_session("../etc", SERVER, projects_dir=projects, claude_projects=claude, run=Runner())
    with pytest.raises(SystemExit):
        move_session("Nope", SERVER, projects_dir=projects, claude_projects=claude, run=Runner())
    with pytest.raises(SystemExit):
        move_session("Orbit", SERVER, session="zzz", projects_dir=projects, claude_projects=claude, run=Runner())


def test_a_session_written_just_now_is_reported_active(mac, tmp_path):
    projects, claude, hist = mac
    os.utime(hist / "abc.jsonl", None)
    r = move_session("Orbit", SERVER, projects_dir=projects, claude_projects=claude, run=Runner())
    assert r["active"] is True


def test_a_failing_step_stops_the_move(mac, tmp_path):
    projects, claude, hist = mac

    class Failing(Runner):
        def __call__(self, args, input=None):
            r = super().__call__(args, input)
            if args[0] == "rsync":
                return SimpleNamespace(returncode=23, stdout="", stderr="rsync: permission denied")
            return r
    run = Failing()
    with pytest.raises(SystemExit, match="permission denied"):
        move_session("Orbit", SERVER, projects_dir=projects, claude_projects=claude, run=run)
    assert not any("new-window" in (c[0][-1] if c[0][0] == "ssh" else "") for c in run.calls)
