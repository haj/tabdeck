from tabdeck.commands import Command, match_name, parse, resolve
from tabdeck.speech import display_names
from tabdeck.status import SessionState, Status, set_status


def test_parse_exact_commands_with_punctuation():
    assert parse("Yes.").kind == "approve"
    assert parse("Approve!").kind == "approve"
    assert parse("What's going on?").kind == "status"
    assert parse("Read more.").kind == "read_more"
    assert parse("Never mind").kind == "cancel"
    assert parse("Open the app.").kind == "open"
    assert parse("   ").kind == "empty"


def test_command_word_inside_longer_sentence_is_a_reply():
    cmd = parse("Yes, but run the tests first.")
    assert cmd == Command("reply", text="Yes, but run the tests first.")


def test_parse_navigation():
    assert parse("Go to Atlas.") == Command("goto", "atlas", "Go to Atlas.")
    assert parse("Tab 3.").kind == "tab" and parse("Tab 3.").arg == "3"
    assert parse("tab three").arg == "three"
    assert parse("New session in Beacon.") == Command("new_session", "beacon", "New session in Beacon.")


def test_match_name():
    opts = {"a": "Atlas", "b": "Atlas 2", "c": "Voice-to-Text-tool"}
    assert match_name("atlas", opts) == "a"
    assert match_name("atlas two", opts) == "b"
    assert match_name("voice to text tool", opts) == "c"
    assert match_name("atlaz", opts) == "a"
    assert match_name("banana", opts) is None


def mk(sid, cwd, status=Status.IDLE, urls=()):
    s = SessionState(session_id=sid, cwd=cwd)
    set_status(s, status, 1)
    s.urls = list(urls)
    return s


def world():
    ss = [mk("a", "/p/Atlas", Status.NEEDS_YOU), mk("b", "/p/Atlas", Status.DONE),
          mk("c", "/p/Beacon", Status.WORKING, ["http://localhost:5173/"])]
    return ss, display_names(ss)


def test_goto_duplicate_names():
    ss, names = world()
    act = resolve(parse("go to Atlas 2"), ss, names, None, [])
    assert act["type"] == "select" and act["session_id"] == "b"
    assert act["speak"].startswith("Atlas 2 is done.")


def test_tab_number_uses_display_order():
    ss, names = world()
    assert resolve(parse("tab two"), ss, names, None, [])["session_id"] == "b"
    assert resolve(parse("tab 9"), ss, names, None, [])["type"] == "ask"


def test_next_skips_selected():
    ss, names = world()
    assert resolve(parse("next"), ss, names, "a", [])["session_id"] == "b"


def test_approve_requires_needs_you():
    ss, names = world()
    ok = resolve(parse("yes"), ss, names, "a", [])
    assert ok == {"type": "keys", "session_id": "a", "key": "enter", "speak": "Approved."}
    assert resolve(parse("yes"), ss, names, "c", [])["type"] == "ask"


def test_commands_needing_selection_ask_when_none():
    ss, names = world()
    assert resolve(parse("stop"), ss, names, None, [])["type"] == "ask"
    assert resolve(parse("fix the header"), ss, names, None, [])["type"] == "ask"


def test_reply_keeps_original_text():
    ss, names = world()
    act = resolve(parse("Yes, but run the tests first."), ss, names, "a", [])
    assert act == {"type": "reply", "session_id": "a", "text": "Yes, but run the tests first.",
                   "speak": "Sending to Atlas."}


def test_open_url():
    ss, names = world()
    assert resolve(parse("open the app"), ss, names, "c", []) == {"type": "open_url", "session_id": "c", "index": 0}
    assert resolve(parse("open the app"), ss, names, "a", [])["type"] == "ask"


def test_new_session_matches_project():
    ss, names = world()
    act = resolve(parse("new session in beacon"), ss, names, None, ["Beacon", "Atlas"])
    assert act == {"type": "new_session", "project": "Beacon", "speak": "Starting Claude in Beacon."}
    assert resolve(parse("new session in zzz"), ss, names, None, ["Beacon"])["type"] == "ask"


def test_status_and_empty():
    ss, names = world()
    assert resolve(parse("status"), ss, names, None, [])["type"] == "speak"
    assert resolve(parse(""), ss, names, None, []) == {"type": "ask", "text": "I didn't catch that."}


from tabdeck.commands import strip_wake


def test_strip_wake_variants():
    assert strip_wake("Jarvis, status.") == "status."
    assert strip_wake("Hey Jarvis go to Atlas") == "go to Atlas"
    assert strip_wake("Okay, Jarvis: approve") == "approve"
    assert strip_wake("OkayJarvis, next") == "next"
    assert strip_wake("Jarvis.") == ""
    assert strip_wake("Hello, my name. Jarvis, switch to garden.") == "switch to garden."
    assert strip_wake("Deck, status.") is None
    assert strip_wake("I told Jarvis about it") is None
    assert strip_wake("Can you pass me the salt?") is None


def test_parse_tell():
    cmd = parse("Tell Atlas to run the tests.")
    assert cmd.kind == "tell" and cmd.arg == "Atlas to run the tests."
    assert parse("Tell me what you did").kind == "reply"


def tell_world():
    ss = [mk("a", "/p/Atlas", Status.NEEDS_YOU), mk("c", "/p/Voice-to-Text-tool"),
          mk("k", "/p/Beacon")]
    return ss, display_names(ss)


def test_resolve_tell_named_tab():
    ss, names = tell_world()
    assert resolve(parse("Tell Atlas to run the tests."), ss, names, None, []) == {
        "type": "reply", "session_id": "a", "text": "run the tests.", "speak": "Sending to Atlas."}


def test_resolve_tell_name_containing_to():
    ss, names = tell_world()
    act = resolve(parse("tell Voice to text tool to run it"), ss, names, None, [])
    assert act["session_id"] == "c" and act["text"] == "run it"


def test_resolve_tell_colon_and_it():
    ss, names = tell_world()
    assert resolve(parse("Ask Beacon: what's the status?"), ss, names, None, [])["session_id"] == "k"
    act = resolve(parse("tell it to stop"), ss, names, "a", [])
    assert act["session_id"] == "a" and act["text"] == "stop"


def test_resolve_tell_unknown_asks():
    ss, names = tell_world()
    assert resolve(parse("tell banana to go"), ss, names, None, [])["type"] == "ask"


def test_always_allow_presses_option_two():
    ss, names = world()
    assert parse("Always allow.").kind == "always"
    assert resolve(parse("always allow"), ss, names, "a", []) == {
        "type": "keys", "session_id": "a", "key": "2", "speak": "Always allowed."}
    assert resolve(parse("always allow"), ss, names, "c", [])["type"] == "ask"


def test_new_session_with_task():
    cmd = parse("Start Claude in Beacon and tell it to fix the failing tests.")
    act = resolve(cmd, [], {}, None, ["Beacon"])
    assert act == {"type": "new_session", "project": "Beacon", "task": "fix the failing tests.",
                   "speak": "Starting Claude in Beacon, then I'll send your task."}
    assert resolve(parse("new session in beacon"), [], {}, None, ["Beacon"]).get("task") is None


def test_new_session_on_my_mac():
    act = resolve(parse("Start a session on my Mac in Beacon."), [], {}, None, ["Beacon"])
    assert act["project"] == "Beacon" and act["where"] == "mac"
    assert "where" not in resolve(parse("start a session in beacon"), [], {}, None, ["Beacon"])


def test_any_wake_word_works():
    from tabdeck.commands import wake_pattern
    friday = wake_pattern("Friday")
    assert strip_wake("Hey Friday, status.", friday) == "status."
    assert strip_wake("Friday: approve", friday) == "approve"
    assert strip_wake("Jarvis, status.", friday) is None
    two = wake_pattern("computer please")
    assert strip_wake("Computer, please go to Atlas", two) == "go to Atlas"
