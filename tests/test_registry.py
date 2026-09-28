import json

from tabdeck.registry import Registry, Snapshot
from tabdeck.status import Status


def snap(sid="A", cwd="/p/Atlas", job="zsh", screen=""):
    return Snapshot(session_id=sid, tab_title="t", cwd=cwd, job_name=job, tty="/dev/ttys001",
                    shell_pid=100, screen_text=screen)


def test_update_adds_and_removes_sessions():
    r = Registry()
    r.update([snap("A"), snap("B")], 1)
    v = r.version
    assert set(r.sessions) == {"A", "B"}
    r.update([snap("A")], 2)
    assert set(r.sessions) == {"A"} and r.version > v


def test_unchanged_update_does_not_bump_version():
    r = Registry()
    r.update([snap("A")], 1)
    v = r.version
    r.update([snap("A")], 2)
    assert r.version == v


def test_screen_urls_accumulate():
    r = Registry()
    r.update([snap(screen="Local: http://localhost:5173/")], 1)
    r.update([snap(screen="cleared")], 2)
    assert r.sessions["A"].screen_urls == ["http://localhost:5173/"]


def test_hook_with_iterm_prefix(tmp_path):
    r = Registry()
    r.update([snap("UUID-1", job="claude")], 1)
    r.apply_hook("w0t1p0:UUID-1", {"hook_event_name": "UserPromptSubmit"}, 2)
    assert r.sessions["UUID-1"].status is Status.WORKING


def test_hook_before_first_poll_is_applied_later():
    r = Registry()
    r.apply_hook("w0t1p0:NEW", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                                "message": "Needs permission"}, 1)
    assert "NEW" not in r.sessions
    r.update([snap("NEW", job="claude")], 2)
    assert r.sessions["NEW"].status is Status.NEEDS_YOU


def test_stop_reads_first_sentence_of_reply(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Fixed the **login** bug. Tests pass. Deployed."}]}}) + "\n")
    r = Registry()
    r.update([snap(job="claude")], 1)
    r.apply_hook("A", {"hook_event_name": "Stop", "transcript_path": str(t)}, 2)
    assert r.sessions["A"].message == "Fixed the login bug. Tests pass."


def test_mark_and_seen():
    r = Registry()
    r.update([snap(job="claude")], 1)
    r.apply_hook("A", {"hook_event_name": "Stop"}, 2)
    assert r.sessions["A"].seen is False
    r.mark_seen("A")
    assert r.sessions["A"].seen is True
    r.mark("A", Status.WORKING, "", 3)
    assert r.sessions["A"].status is Status.WORKING


def test_url_prefs_persist(tmp_path):
    prefs = tmp_path / "state.json"
    r = Registry(prefs)
    r.update([snap()], 1)
    r.set_urls("A", ["http://localhost:5173/"])
    r.pin_url("A", "http://localhost:3000/admin")
    r.hide_url("A", "http://localhost:5173/")
    assert r.sessions["A"].urls == ["http://localhost:3000/admin"]
    r2 = Registry(prefs)
    assert r2.prefs_for("/p/Atlas") == (["http://localhost:3000/admin"], ["http://localhost:5173/"])
    r2.update([snap()], 1)
    r2.reset_urls("A")
    assert r2.prefs_for("/p/Atlas") == ([], [])


def test_ordered_and_names():
    r = Registry()
    r.update([snap("A", cwd="/p/Atlas"), snap("B", cwd="/p/Atlas", job="claude")], 1)
    r.apply_hook("B", {"hook_event_name": "Notification", "message": "x"}, 2)
    assert [s.session_id for s in r.ordered()] == ["B", "A"]
    assert r.names() == {"A": "Atlas", "B": "Atlas 2"}


def test_active_tab_tracks_iterm_focus():
    r = Registry()
    r.update([snap("A"), snap("B")], 1)
    v = r.version
    r.set_active("B")
    assert r.active == "B" and r.version > v
    v = r.version
    r.set_active("B")
    assert r.version == v
    r.set_active("GONE")
    assert r.active is None


def test_late_event_is_dropped_when_a_newer_one_arrived():
    r = Registry()
    r.update([snap("A", job="claude")], 1)
    seq = r.hook_seq("A")
    r.apply_hook("A", {"hook_event_name": "UserPromptSubmit"}, 2)
    r.apply_hook("A", {"hook_event_name": "Stop", "summary": "old"}, 3, if_seq=seq)
    assert r.sessions["A"].status is Status.WORKING
    seq = r.hook_seq("A")
    r.apply_hook("A", {"hook_event_name": "Stop", "summary": "new"}, 4, if_seq=seq)
    assert r.sessions["A"].status is Status.DONE and r.sessions["A"].message == "new"


def test_activity_log_records_attention_events():
    r = Registry()
    r.update([snap("A", job="claude")], 1)
    r.apply_hook("A", {"hook_event_name": "UserPromptSubmit"}, 2)
    r.apply_hook("A", {"hook_event_name": "Stop", "summary": "Added login."}, 3)
    r.record("sent", "A", "run the tests", 4)
    assert [(e["kind"], e["tab"], e["text"]) for e in r.activity(since=0)] == [
        ("done", "Atlas", "Added login."), ("sent", "Atlas", "run the tests")]
    assert r.activity(since=3.5)[0]["kind"] == "sent"


def test_remote_snapshot_fields_are_kept():
    r = Registry()
    r.update([Snapshot("mac-A", "t", "/p/F", "claude", "", 1, "", offline=True, urls=("http://localhost:3000/",),
                       url_host="192.0.2.20")], 1)
    s = r.sessions["mac-A"]
    assert s.offline and s.urls == ["http://localhost:3000/"] and s.url_host == "192.0.2.20"


def test_update_copies_server_and_pane():
    r = Registry()
    r.update([Snapshot("tmux-3", "edx", "/p/edx", "claude", "", 1, "", server="hub1", pane="%3")], 1)
    assert (r.sessions["tmux-3"].server, r.sessions["tmux-3"].pane) == ("hub1", "%3")


def test_a_server_tag_change_counts_as_a_change():
    r = Registry()
    r.update([Snapshot("A", "demo", "/p/demo", "claude", "", 1, "")], 1)
    v = r.version
    r.update([Snapshot("A", "demo", "/p/demo", "claude", "", 1, "", server="hub1", pane="%2")], 2)
    assert r.version > v  # the agent re-sends its snapshot at once, so the hub drops the duplicate
