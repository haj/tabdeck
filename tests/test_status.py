from tabdeck.status import (
    SessionState, Status, apply_heuristics, apply_hook, set_status, sort_key,
)


def s(**kw):
    return SessionState(session_id=kw.pop("session_id", "A"), **kw)


def test_project_is_cwd_basename():
    assert s(cwd="/Users/h/Projects/Atlas").project == "Atlas"
    assert s(cwd="", tab_title="zsh").project == "zsh"


def test_prompt_submit_is_working():
    st = s()
    apply_hook(st, {"hook_event_name": "UserPromptSubmit"}, 10)
    assert st.status is Status.WORKING and st.hook_seen and st.status_since == 10


def test_permission_notification_needs_you_and_unseen():
    st = s()
    apply_hook(st, {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                    "message": "Claude needs your permission to use Bash"}, 5)
    assert st.status is Status.NEEDS_YOU
    assert st.message == "Claude needs your permission to use Bash"
    assert st.seen is False


def test_idle_prompt_notification_is_ignored():
    st = s()
    apply_hook(st, {"hook_event_name": "Stop"}, 1)
    apply_hook(st, {"hook_event_name": "Notification", "notification_type": "idle_prompt",
                    "message": "Claude is waiting for your input"}, 70)
    assert st.status is Status.DONE and st.status_since == 1


def test_stop_is_done_and_keeps_transcript_path():
    st = s()
    apply_hook(st, {"hook_event_name": "Stop", "transcript_path": "/t.jsonl"}, 3)
    assert st.status is Status.DONE and st.transcript_path == "/t.jsonl" and not st.seen


def test_session_end_resets():
    st = s(job_name="claude")
    apply_hook(st, {"hook_event_name": "Stop"}, 1)
    apply_hook(st, {"hook_event_name": "SessionEnd"}, 2)
    assert st.status is Status.IDLE and not st.hook_seen


def test_status_since_only_changes_on_transition():
    st = s()
    apply_hook(st, {"hook_event_name": "PreToolUse"}, 1)
    apply_hook(st, {"hook_event_name": "PostToolUse"}, 2)
    assert st.status_since == 1


def test_heuristics_shell_vs_job():
    st = s(job_name="zsh")
    apply_heuristics(st, 1)
    assert st.status is Status.IDLE
    st.job_name = "npm"
    apply_heuristics(st, 2)
    assert st.status is Status.WORKING


def test_heuristics_do_not_override_hook_status():
    st = s(job_name="claude")
    apply_hook(st, {"hook_event_name": "Stop"}, 1)
    apply_heuristics(st, 2)
    assert st.status is Status.DONE


def test_claude_exit_clears_hook_state():
    st = s(job_name="claude")
    apply_hook(st, {"hook_event_name": "Stop"}, 1)
    st.job_name = "zsh"
    apply_heuristics(st, 5)
    assert st.status is Status.IDLE and not st.hook_seen


def test_sort_order():
    need = s(session_id="n"); set_status(need, Status.NEEDS_YOU, 1)
    done_new = s(session_id="d"); set_status(done_new, Status.DONE, 2)
    done_seen = s(session_id="ds"); set_status(done_seen, Status.DONE, 3); done_seen.seen = True
    work = s(session_id="w"); set_status(work, Status.WORKING, 4)
    idle = s(session_id="i")
    ordered = sorted([idle, work, done_seen, done_new, need], key=sort_key)
    assert [x.session_id for x in ordered] == ["n", "d", "w", "ds", "i"]


def test_permission_prompt_names_the_pending_tool():
    st = s(job_name="claude")
    apply_hook(st, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "tool_input": {"command": "npm install stripe", "description": "Install"}}, 1)
    apply_hook(st, {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                    "message": "Claude needs your permission to use Bash"}, 2)
    assert st.status is Status.NEEDS_YOU and st.message == "wants to run: npm install stripe"


def test_describe_tool_variants():
    from tabdeck.status import describe_tool
    assert describe_tool("Edit", {"file_path": "/p/Atlas/src/app.py"}) == "edit app.py"
    assert describe_tool("Write", {"file_path": "/p/x/README.md"}) == "create README.md"
    assert describe_tool("WebFetch", {"url": "https://docs.python.org/3/x"}) == "fetch docs.python.org"
    assert describe_tool("Bash", {"command": "x" * 300}).endswith("…") and len(describe_tool("Bash", {"command": "x" * 300})) < 140
    assert describe_tool("mcp__github__create_pr", {}) == "use mcp github create pr"


def test_tool_is_forgotten_after_it_runs():
    st = s(job_name="claude")
    apply_hook(st, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}, 1)
    apply_hook(st, {"hook_event_name": "PostToolUse", "tool_name": "Bash"}, 2)
    apply_hook(st, {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                    "message": "Claude needs your permission"}, 3)
    assert st.message == "Claude needs your permission"
