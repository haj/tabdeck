import json
import time
import asyncio
import pytest
from fastapi.testclient import TestClient

from tabdeck.auth import Auth
from tabdeck.config import Config
from tabdeck.registry import Registry, Snapshot
from tabdeck.status import Status
from tabdeck.web import COOKIE, create_app

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("192.0.2.21", 5000)


class FakeBridge:
    def __init__(self):
        self.sent, self.keys, self.created = [], [], []

    async def send_text(self, sid, text):
        self.sent.append((sid, text))

    async def send_keys(self, sid, keys):
        self.keys.append((sid, keys))

    async def create_tab(self, cwd, command):
        self.created.append((cwd, command))
        return "NEWID"

    async def snapshot(self):  # the hub's own (local) tabs, as the fixture registered them
        return [Snapshot("A", "claude", "/p/Atlas", "claude", "/dev/ttys1", 1, "line1\nhttp://localhost:5173/")]

    async def active_session(self):
        return None

    async def focus(self, sid):
        if sid == "GONE":
            raise KeyError(sid)
        self.focused = getattr(self, "focused", []) + [sid]


class FakeTranscriber:
    text = "approve"

    def transcribe(self, audio, suffix, prompt):
        self.last = (audio, suffix, prompt)
        return self.text


@pytest.fixture
def env(tmp_path):
    (tmp_path / "Projects" / "Beacon").mkdir(parents=True)
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.20")
    registry = Registry()
    registry.update([Snapshot("A", "claude", "/p/Atlas", "claude", "/dev/ttys1", 1,
                              "line1\nhttp://localhost:5173/")], 1)
    registry.set_urls("A", ["http://localhost:5173/"])
    bridge, transcriber = FakeBridge(), FakeTranscriber()
    auth = Auth(config.tokens_file)
    app = create_app(registry=registry, bridge=bridge, auth=auth, transcriber=transcriber,
                     config=config, clock=lambda: 100.0)
    local = TestClient(app, base_url="https://testserver", client=LOCAL)
    remote = TestClient(app, base_url="https://testserver", client=REMOTE)
    return dict(registry=registry, bridge=bridge, transcriber=transcriber, auth=auth,
                local=local, remote=remote)


def test_remote_unpaired_is_rejected(env):
    assert env["remote"].get("/api/state").status_code == 401
    assert env["remote"].get("/").status_code == 401


def test_local_state_uses_localhost_urls(env):
    body = env["local"].get("/api/state").json()
    assert body["is_local"] is True
    assert body["sessions"][0]["name"] == "Atlas"
    assert body["sessions"][0]["urls"] == [{"url": "http://localhost:5173/", "href": "http://localhost:5173/"}]


def test_pairing_then_remote_gets_rewritten_urls(env):
    url = env["local"].post("/api/pair").json()["url"]
    assert url.startswith("https://192.0.2.20:8765/pair?code=")
    code = url.split("code=")[1]
    r = env["remote"].get(f"/pair?code={code}", follow_redirects=False)
    assert r.status_code == 303 and COOKIE in r.cookies
    body = env["remote"].get("/api/state").json()
    assert body["is_local"] is False
    assert body["sessions"][0]["urls"][0]["href"] == "http://192.0.2.20:5173/"
    assert env["remote"].get(f"/pair?code={code}").status_code == 403


def test_pair_and_hook_are_local_only(env):
    assert env["remote"].post("/api/pair").status_code == 403
    assert env["remote"].post("/hook", json={}).status_code == 403


def test_hook_without_iterm_header_is_ignored(env):
    assert env["local"].post("/hook", json={"hook_event_name": "Stop"}).status_code == 204


def test_hook_updates_status(env):
    r = env["local"].post("/hook", json={"hook_event_name": "UserPromptSubmit"},
                          headers={"X-Iterm-Session": "w0t0p0:A"})
    assert r.status_code == 204
    assert env["registry"].sessions["A"].status is Status.WORKING


def test_send_types_text_and_marks_working(env):
    assert env["local"].post("/api/sessions/A/send", json={"text": "run tests"}).json() == {"ok": True}
    assert env["bridge"].sent == [("A", "run tests")]
    assert env["registry"].sessions["A"].status is Status.WORKING


def test_send_to_closed_tab_is_404(env):
    assert env["local"].post("/api/sessions/GONE/send", json={"text": "x"}).status_code == 404
    assert env["bridge"].sent == []


def test_keys(env):
    assert env["local"].post("/api/sessions/A/keys", json={"key": "rm -rf"}).status_code == 400
    env["registry"].apply_hook("A", {"hook_event_name": "UserPromptSubmit"}, 2)
    env["local"].post("/api/sessions/A/keys", json={"key": "escape"})
    assert env["bridge"].keys == [("A", "\x1b")]
    assert env["registry"].sessions["A"].status is Status.DONE


def test_command_endpoint(env):
    body = env["local"].post("/api/command", json={"text": "status", "selected": ""}).json()
    assert body["action"]["type"] == "speak"


def test_voice_endpoint(env):
    env["registry"].apply_hook("A", {"hook_event_name": "Notification", "message": "perm"}, 2)
    r = env["local"].post("/api/voice", data={"selected": "A"},
                          files={"audio": ("speech.m4a", b"\x00" * 2000, "audio/mp4")})
    body = r.json()
    assert body["text"] == "approve"
    assert body["action"] == {"type": "keys", "session_id": "A", "key": "enter", "speak": "Approved."}
    assert env["transcriber"].last[1] == ".m4a"
    assert "Atlas" in env["transcriber"].last[2]


def test_new_session(env):
    assert env["local"].post("/api/new_session", json={"project": "Nope"}).status_code == 404
    assert env["local"].post("/api/new_session", json={"project": "Beacon"}).json() == {"id": "NEWID"}
    assert env["bridge"].created[0][1] == "claude"


def test_screen_and_reply(env):
    assert "line1" in env["local"].get("/api/sessions/A/screen").json()["text"]
    assert env["local"].get("/api/sessions/A/reply").json() == {"text": "", "chunks": []}


def test_reply_is_read_in_five_sentence_chunks(env, tmp_path):
    import json as _json
    t = tmp_path / "t.jsonl"
    t.write_text(_json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": " ".join(f"Sentence {i}." for i in range(1, 8))}]}}) + "\n")
    env["registry"].sessions["A"].transcript_path = str(t)
    chunks = env["local"].get("/api/sessions/A/reply").json()["chunks"]
    assert len(chunks) == 2 and chunks[0].endswith("Sentence 5.")


def test_websocket_sends_state(env):
    with env["local"].websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "state" and msg["sessions"][0]["id"] == "A"


def test_cross_origin_requests_are_rejected(env):
    evil = {"Origin": "https://evil.example"}
    assert env["local"].get("/api/state", headers=evil).status_code == 401
    assert env["local"].post("/api/revoke", headers=evil).status_code == 403
    assert env["local"].get("/api/state", headers={"Origin": "https://testserver"}).status_code == 200
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with env["local"].websocket_connect("/ws", headers=evil) as ws:
            ws.receive_json()


def test_tab_closed_before_registry_noticed_is_404(env):
    async def gone(sid, *args):
        raise KeyError(sid)
    env["bridge"].send_text = gone
    env["bridge"].send_keys = gone
    assert env["local"].post("/api/sessions/A/send", json={"text": "x"}).status_code == 404
    assert env["local"].post("/api/sessions/A/keys", json={"key": "enter"}).status_code == 404


def voice(env, text, **fields):
    env["transcriber"].text = text
    return env["local"].post("/api/voice", data=fields,
                             files={"audio": ("speech.wav", b"\x00" * 2000, "audio/wav")}).json()


def test_wake_mode_ignores_speech_without_wake_word(env):
    assert voice(env, "Can you check the oven?", wake="1") == {"text": "", "heard": False, "action": None}


def test_wake_mode_strips_wake_word(env):
    body = voice(env, "Jarvis, status.", wake="1")
    assert body["heard"] is True and body["text"] == "status." and body["action"]["type"] == "speak"


def test_bare_wake_word_answers_yes(env):
    assert voice(env, "Jarvis.", wake="1", final="1")["action"] == {"type": "speak", "text": "Yes?"}


def test_followup_accepts_without_wake_word(env):
    env["registry"].apply_hook("A", {"hook_event_name": "Notification", "message": "perm"}, 2)
    body = voice(env, "Approve.", wake="1", followup="1", selected="A")
    assert body["action"]["type"] == "keys"


def test_followup_ignores_empty_transcript(env):
    assert voice(env, "", wake="1", followup="1")["heard"] is False


def test_prompt_mentions_wake_word(env):
    voice(env, "Jarvis, status.", wake="1")
    prompt = env["transcriber"].last[2]
    assert prompt == "The assistant is called Jarvis. Tabs: Atlas."


class FakeInterpreter:
    def __init__(self, result, polished=None):
        self.result, self.calls, self.polished, self.polish_calls = result, [], polished, []

    def interpret(self, text, sessions, names, selected, length="normal"):
        self.calls.append(text)
        return self.result

    def polish(self, text, tab, context):
        self.polish_calls.append((text, tab))
        return self.polished


def llm_client(env, tmp_path, result, polished=None):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.20")
    interp = FakeInterpreter(result, polished)
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"],
                     transcriber=env["transcriber"], config=config, interpreter=interp)
    return TestClient(app, base_url="https://testserver", client=LOCAL), interp


def llm_voice(client, env, text, **fields):
    env["transcriber"].text = text
    return client.post("/api/voice", data=fields,
                       files={"audio": ("speech.wav", b"\x00" * 2000, "audio/wav")}).json()


def test_llm_interprets_after_wake_word(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "reply", "tab": "Atlas", "text": "push it"})
    body = llm_voice(client, env, "Jarvis, tell the Atlas one to push it", wake="1")
    assert interp.calls == ["tell the Atlas one to push it"]
    assert body["heard"] is True and body["action"]["type"] == "reply" and body["action"]["text"] == "push it"


def test_llm_none_is_ignored(env, tmp_path):
    client, _ = llm_client(env, tmp_path, {"action": "none"})
    assert llm_voice(client, env, "can you pass the salt", wake="1", followup="1")["heard"] is False


def test_llm_not_called_without_wake_word_outside_followup(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "status"})
    assert llm_voice(client, env, "just chatting", wake="1")["heard"] is False
    assert interp.calls == []


def test_llm_down_followup_never_becomes_reply(env, tmp_path):
    client, _ = llm_client(env, tmp_path, None)
    assert llm_voice(client, env, "can you pass the salt", wake="1", followup="1", selected="A")["heard"] is False
    assert llm_voice(client, env, "status", wake="1", followup="1")["action"]["type"] == "speak"


def test_llm_down_with_wake_word_uses_rules(env, tmp_path):
    client, _ = llm_client(env, tmp_path, None)
    body = llm_voice(client, env, "Jarvis, fix the header", wake="1", selected="A")
    assert body["action"]["type"] == "reply"


def test_typed_commands_skip_the_llm(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "status"})
    client.post("/api/command", json={"text": "fix the header", "selected": "A"})
    assert interp.calls == []


def test_focus_brings_tab_to_front(env):
    assert env["local"].post("/api/sessions/A/focus").json() == {"ok": True}
    assert env["bridge"].focused == ["A"]
    assert env["local"].post("/api/sessions/GONE/focus").status_code == 404
    assert env["remote"].post("/api/sessions/A/focus").status_code == 401


def test_compose_followup_sends_speech_as_reply(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "read"})
    body = llm_voice(client, env, "run the tests and fix failures", wake="1", followup="1", compose="1", selected="A")
    assert interp.calls == []
    assert body["action"] == {"type": "reply", "session_id": "A", "text": "run the tests and fix failures",
                              "speak": "Sending to Atlas."}
    assert llm_voice(client, env, "never mind", wake="1", followup="1", compose="1", selected="A")["action"] == {"type": "cancel"}


def test_reply_is_cleaned_up_before_sending(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "reply", "tab": "Atlas", "text": "um run the uh tests"},
                                {"message": "Run the tests.", "confident": True, "question": ""})
    body = llm_voice(client, env, "Jarvis, tell Atlas um run the uh tests", wake="1")
    assert body["action"]["type"] == "reply" and body["action"]["text"] == "Run the tests."
    assert interp.polish_calls == [("um run the uh tests", "Atlas")]


def test_unclear_reply_asks_back(env, tmp_path):
    client, _ = llm_client(env, tmp_path, {"action": "reply", "tab": "Atlas", "text": "run the test fail"},
                           {"message": "Run the tests and fix failures.", "confident": False,
                            "question": "Did you mean: run the tests and fix failures?"})
    body = llm_voice(client, env, "Jarvis, tell Atlas run the test fail", wake="1")
    assert body["action"] == {"type": "clarify", "session_id": "A", "text": "Run the tests and fix failures.",
                              "speak": "Did you mean: run the tests and fix failures?"}


def test_compose_message_is_cleaned_up_too(env, tmp_path):
    client, _ = llm_client(env, tmp_path, None, {"message": "Add a login page.", "confident": True, "question": ""})
    body = llm_voice(client, env, "uh add a a login page", wake="1", followup="1", compose="1", selected="A")
    assert body["action"]["text"] == "Add a login page."


def test_answer_to_clarifying_question(env, tmp_path):
    client, _ = llm_client(env, tmp_path, None, {"message": "Push it.", "confident": True, "question": ""})
    ans = lambda text: llm_voice(client, env, text, wake="1", followup="1", confirming="1", selected="A")["action"]
    assert ans("Yes.") == {"type": "confirmed"}
    assert ans("yeah send it") == {"type": "confirmed"}
    assert ans("No.") == {"type": "cancel"}
    assert ans("push it to main instead")["type"] == "reply"


def test_polish_unavailable_sends_as_heard(env, tmp_path):
    client, _ = llm_client(env, tmp_path, {"action": "reply", "text": "run the tests"}, None)
    body = llm_voice(client, env, "Jarvis, run the tests", wake="1", selected="A")
    assert body["action"]["text"] == "run the tests"


def test_dangling_message_always_asks(env, tmp_path):
    client, _ = llm_client(env, tmp_path, {"action": "reply", "text": "fix the thing with the"},
                           {"message": "Fix the thing with the", "confident": True, "question": ""})
    body = llm_voice(client, env, "Jarvis, fix the thing with the uh", wake="1", selected="A", final="1")
    assert body["action"]["type"] == "clarify"
    assert body["action"]["speak"] == "Fix the thing with the what? Say the whole message again."


def test_unfinished_speech_asks_widget_to_keep_listening(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "status"})
    assert llm_voice(client, env, "Jarvis.", wake="1") == {"text": "", "heard": True, "action": None, "incomplete": True}
    assert llm_voice(client, env, "Jarvis, tell Atlas to", wake="1")["incomplete"] is True
    assert interp.calls == []
    assert llm_voice(client, env, "Jarvis.", wake="1", final="1")["action"] == {"type": "speak", "text": "Yes?"}
    assert llm_voice(client, env, "Jarvis, what's going on?", wake="1")["action"]["type"] == "speak"


def test_complete_sentences_ending_in_short_words_are_not_held(env, tmp_path):
    client, _ = llm_client(env, tmp_path, {"action": "status"})
    for text in ["Jarvis, what's going on?", "Jarvis, turn it on", "Jarvis, fix that", "Jarvis, log in"]:
        assert "incomplete" not in llm_voice(client, env, text, wake="1"), text


def test_voice_passes_answer_length_to_model(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "status"})
    seen = {}
    interp.interpret = lambda text, s, n, sel, length="normal": seen.setdefault("length", length) and {"action": "status"}
    llm_voice(client, env, "Jarvis, what's going on?", wake="1", length="detailed")
    assert seen["length"] == "detailed"


def test_state_reports_active_iterm_tab(env):
    env["registry"].set_active("A")
    assert env["local"].get("/api/state").json()["active"] == "A"


def test_stop_hook_uses_model_summary(env, tmp_path):
    import json as _json
    t = tmp_path / "t.jsonl"
    t.write_text(_json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I refactored everything. " + "Here is a long explanation of each step. " * 12}]}}) + "\n")
    client, interp = llm_client(env, tmp_path, None)
    interp.summarize = lambda text, tab: "Refactored the auth module. All tests pass." if "refactored" in text else None
    r = client.post("/hook", json={"hook_event_name": "Stop", "transcript_path": str(t)},
                    headers={"X-Iterm-Session": "w0t0p0:A"})
    assert r.status_code == 204
    s = env["registry"].sessions["A"]
    assert s.status is Status.DONE and s.message == "Refactored the auth module. All tests pass."


def test_stop_hook_without_summary_falls_back(env, tmp_path):
    import json as _json
    t = tmp_path / "t.jsonl"
    t.write_text(_json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Done. Tests pass. Deployed."}]}}) + "\n")
    client, interp = llm_client(env, tmp_path, None)
    interp.summarize = lambda text, tab: None
    client.post("/hook", json={"hook_event_name": "Stop", "transcript_path": str(t)}, headers={"X-Iterm-Session": "A"})
    assert env["registry"].sessions["A"].message == "Done. Tests pass."


def test_catch_me_up_digests_activity_since_last_time(env):
    env["registry"].record("done", "A", "Added the login page.", 50)
    env["local"].post("/api/sessions/A/send", json={"text": "deploy it"})
    body = env["local"].post("/api/command", json={"text": "catch me up", "selected": ""}).json()
    assert body["action"]["type"] == "speak"
    assert "Atlas finished: Added the login page." in body["action"]["text"]
    assert "You sent Atlas: deploy it." in body["action"]["text"]
    again = env["local"].post("/api/command", json={"text": "what did I miss", "selected": ""}).json()
    assert again["action"]["text"] == "Nothing new since your last catch-up."


def test_new_session_sends_task_once_claude_is_ready(env):
    async def create_tab(cwd, command):
        env["registry"].update(list(_snaps(env)) + [Snapshot("NEWID", "claude", cwd, "claude", "/dev/ttys9", 9, "")], 1)
        env["registry"].apply_hook("NEWID", {"hook_event_name": "SessionStart"}, 1)
        return "NEWID"
    env["bridge"].create_tab = create_tab
    r = env["local"].post("/api/new_session", json={"project": "Beacon", "task": "fix the failing tests"})
    assert r.json() == {"id": "NEWID"}
    assert env["bridge"].sent == [("NEWID", "fix the failing tests")]


def test_new_session_task_not_sent_into_a_plain_shell(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.20")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"],
                     transcriber=env["transcriber"], config=config, ready_timeout=0.3)
    client = TestClient(app, base_url="https://testserver", client=LOCAL)
    client.post("/api/new_session", json={"project": "Beacon", "task": "fix it"})
    assert env["bridge"].sent == []


def _snaps(env):
    for s in env["registry"].sessions.values():
        yield Snapshot(s.session_id, s.tab_title, s.cwd, s.job_name, s.tty, s.shell_pid, s.screen_text)


def test_short_reply_is_spoken_as_is_not_summarised(env, tmp_path):
    import json as _json
    t = tmp_path / "t.jsonl"
    t.write_text(_json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Done, created the file."}]}}) + "\n")
    client, interp = llm_client(env, tmp_path, None)
    calls = []
    interp.summarize = lambda text, tab: calls.append(text) or "The session concluded without output."
    client.post("/hook", json={"hook_event_name": "Stop", "transcript_path": str(t)}, headers={"X-Iterm-Session": "A"})
    assert calls == [] and env["registry"].sessions["A"].message == "Done, created the file."


def test_messages_are_never_typed_into_a_bare_shell(env):
    env["registry"].update([Snapshot("A", "zsh", "/p/Atlas", "zsh", "/dev/ttys1", 1, "")], 5)
    r = env["local"].post("/api/sessions/A/send", json={"text": "remove the build folder"})
    assert r.status_code == 409 and "isn't running" in r.json()["detail"]
    assert env["bridge"].sent == []


def test_stop_prefers_answer_text_from_the_hook_event(env, tmp_path):
    client, interp = llm_client(env, tmp_path, None)
    seen = []
    interp.summarize = lambda text, tab: seen.append(text) or "Summary of the new answer."
    long_answer = "The brand new answer. " * 20
    client.post("/hook", json={"hook_event_name": "Stop", "last_assistant_message": long_answer},
                headers={"X-Iterm-Session": "A"})
    assert seen == [long_answer] and env["registry"].sessions["A"].message == "Summary of the new answer."


def test_stop_waits_for_the_answer_to_be_written(env, tmp_path):
    import json as _json
    import threading
    t = tmp_path / "t.jsonl"
    old = [{"type": "user", "message": {"content": "q1"}},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": "Old answer."}]}},
           {"type": "user", "message": {"content": "q2"}}]
    t.write_text("\n".join(_json.dumps(e) for e in old) + "\n")
    def append_later():
        with open(t, "a") as f:
            f.write(_json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "New answer."}]}}) + "\n")
    threading.Timer(0.4, append_later).start()
    client, interp = llm_client(env, tmp_path, None)
    interp.summarize = lambda text, tab: None
    client.post("/hook", json={"hook_event_name": "Stop", "transcript_path": str(t)}, headers={"X-Iterm-Session": "A"})
    assert env["registry"].sessions["A"].message == "New answer."


def test_voice_without_transcriber_is_503_and_state_names_source(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", source="tmux")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None, config=config)
    client = TestClient(app, base_url="https://testserver", client=LOCAL)
    r = client.post("/api/voice", files={"audio": ("s.wav", b"\x00" * 2000, "audio/wav")})
    assert r.status_code == 503
    assert client.get("/api/state").json()["source"] == "tmux"


def test_hook_with_tmux_pane_maps_to_session(env):
    env["registry"].update([Snapshot("tmux-3", "Atlas", "/p/Atlas", "claude", "/dev/pts/2", 1, "")], 1)
    env["local"].post("/hook", json={"hook_event_name": "Notification", "message": "perm"},
                      headers={"X-Tmux-Pane": "%3"})
    assert env["registry"].sessions["tmux-3"].status is Status.NEEDS_YOU


def test_utterance_endpoint_matches_voice_behaviour(env, tmp_path):
    client, interp = llm_client(env, tmp_path, {"action": "status"})
    body = client.post("/api/utterance", json={"text": "Jarvis, what's going on?", "wake": "1"}).json()
    assert body["heard"] is True and body["action"]["type"] == "speak"
    assert client.post("/api/utterance", json={"text": "pass the salt", "wake": "1"}).json()["heard"] is False
    assert client.post("/api/utterance", json={"text": "Jarvis.", "wake": "1"}).json()["incomplete"] is True


def test_text_to_tmux_shell_pane_is_refused(env):
    env["registry"].update([Snapshot("tmux-5", "scratch", "/home/dev", "bash", "/dev/pts/3", 1, "")], 1)
    assert env["local"].post("/api/sessions/tmux-5/send", json={"text": "hello"}).status_code == 409


def test_new_session_clones_missing_project(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects")
    (tmp_path / "projects.json").write_text('{"Atlas": "git@github.com:h/Atlas.git"}')
    cloned = []

    async def git_clone(remote, dest):
        cloned.append((remote, dest))
        dest.mkdir(parents=True)
        return None
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None,
                     config=config, git_clone=git_clone)
    client = TestClient(app, base_url="https://testserver", client=LOCAL)
    assert "Atlas" in client.get("/api/projects").json()["projects"]
    assert client.post("/api/new_session", json={"project": "Atlas"}).json() == {"id": "NEWID"}
    assert cloned == [("git@github.com:h/Atlas.git", tmp_path / "Projects" / "Atlas")]


def test_new_session_clone_failure_is_reported(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects")
    (tmp_path / "projects.json").write_text('{"Atlas": "git@github.com:h/Atlas.git"}')

    async def git_clone(remote, dest):
        return "Permission denied (publickey)."
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None,
                     config=config, git_clone=git_clone)
    r = TestClient(app, base_url="https://testserver", client=LOCAL).post("/api/new_session", json={"project": "Atlas"})
    assert r.status_code == 502 and r.json()["detail"] == "I couldn't clone Atlas: Permission denied (publickey)."


def test_new_session_rejects_unsafe_names(env):
    assert env["local"].post("/api/new_session", json={"project": "../etc"}).status_code == 404


TRUST_SCREEN = "Accessing workspace:\n ❯ No, exit\n   Yes, I trust this folder\n Enter to confirm · Esc to cancel"


def test_new_session_answers_trust_prompt_before_sending_task(env):
    keys = []

    async def create_tab(cwd, command):
        env["registry"].update(list(_snaps(env)) + [Snapshot("NEWID", "claude", cwd, "claude", "/dev/ttys9", 9, TRUST_SCREEN)], 1)
        return "NEWID"

    async def send_keys(sid, k):
        keys.append(k)
        if k == "\r":  # trust accepted: Claude shows its prompt
            env["registry"].update([Snapshot(s.session_id, s.tab_title, s.cwd, s.job_name, s.tty, s.shell_pid,
                                             "❯ " if s.session_id == "NEWID" else s.screen_text)
                                    for s in env["registry"].sessions.values()], 2)

    env["bridge"].create_tab, env["bridge"].send_keys = create_tab, send_keys
    env["local"].post("/api/new_session", json={"project": "Beacon", "task": "fix the tests"})
    assert keys == ["\x1b[B", "\r"]
    assert env["bridge"].sent == [("NEWID", "fix the tests")]


def test_task_not_sent_if_trust_prompt_appears_during_settle(env):
    screens = iter(["", TRUST_SCREEN])  # first check: nothing drawn yet; after settle: the prompt

    async def create_tab(cwd, command):
        env["registry"].update(list(_snaps(env)) + [Snapshot("NEWID", "claude", cwd, "claude", "/dev/ttys9", 9, "")], 1)
        return "NEWID"
    orig_sleep = asyncio.sleep

    async def sleepy(t):
        s = env["registry"].sessions.get("NEWID")
        if s is not None and t >= 1.0:  # the settle before sending: Claude draws the trust prompt now
            s.screen_text = next(screens, s.screen_text)
        await orig_sleep(0)

    import tabdeck.web as web_mod
    config = Config(data_dir=env["auth"].file.parent, projects_dir=env["auth"].file.parent / "Projects")
    env["bridge"].create_tab = create_tab
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None,
                     config=config, ready_timeout=0.2)
    web_mod.asyncio.sleep = sleepy
    try:
        TestClient(app, base_url="https://testserver", client=LOCAL).post(
            "/api/new_session", json={"project": "Beacon", "task": "fix it"})
    finally:
        web_mod.asyncio.sleep = orig_sleep
    assert env["bridge"].sent == []


def test_stale_hook_state_of_reused_id_is_cleared(env):
    env["registry"].update(list(_snaps(env)) + [Snapshot("NEWID", "old", "/p/x", "zsh", "/dev/ttys9", 9, "")], 1)
    env["registry"].apply_hook("NEWID", {"hook_event_name": "SessionStart"}, 1)

    async def create_tab(cwd, command):
        return "NEWID"
    env["bridge"].create_tab = create_tab
    env["local"].post("/api/new_session", json={"project": "Beacon"})
    assert env["registry"].sessions["NEWID"].hook_seen is False


def test_bearer_agent_token_authorizes_remote_requests(env, tmp_path):
    from tabdeck.agents import AgentTokens
    tokens = AgentTokens(tmp_path / "agents.json")
    token = tokens.issue("mac")
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None,
                     config=config, agents=tokens)
    remote = TestClient(app, base_url="https://testserver", client=REMOTE)
    assert remote.get("/api/state").status_code == 401
    assert remote.get("/api/state", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert remote.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401


import threading


def agent_env(env, tmp_path):
    from tabdeck.agents import AgentTokens
    from tabdeck.composite import CompositeSource
    from tabdeck.remote_source import RemoteSource
    tokens = AgentTokens(tmp_path / "agents.json")
    token = tokens.issue("mac")
    remote = RemoteSource(timeout=2)
    bridge = CompositeSource(env["bridge"], {"mac": remote})
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.10")
    app = create_app(registry=env["registry"], bridge=bridge, auth=env["auth"], transcriber=None, config=config,
                     agents=tokens, remotes={"mac": remote})
    return TestClient(app, base_url="https://testserver", client=LOCAL), token, remote


MAC_SNAP = {"type": "snapshot", "active": None, "projects": ["Atlas"], "sessions": [
    {"id": "mac-A", "title": "claude", "cwd": "/Users/h/Projects/Atlas", "job": "claude", "tty": "/dev/ttys1", "pid": 5,
     "screen": "", "urls": [{"url": "http://localhost:5173/"}]}]}


def test_agent_socket_requires_token(env, tmp_path):
    from starlette.websockets import WebSocketDisconnect
    client, token, _ = agent_env(env, tmp_path)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/agent", headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_json()


class LiveHub:
    """The hub on a real uvicorn server in a thread: one event loop, like production.
    (TestClient runs WebSockets in a separate loop, so a request could not use the agent socket.)"""

    def __init__(self, app):
        import socket
        import uvicorn
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.01)
        self.base = f"http://127.0.0.1:{self.port}"

    def agent(self, token):
        from websockets.sync.client import connect
        return connect(f"ws://127.0.0.1:{self.port}/agent", additional_headers={"Authorization": f"Bearer {token}"})

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


def live_agent_env(env, tmp_path):
    from tabdeck.agents import AgentTokens
    from tabdeck.composite import CompositeSource
    from tabdeck.remote_source import RemoteSource
    tokens = AgentTokens(tmp_path / "agents.json")
    token = tokens.issue("mac")
    remote = RemoteSource(timeout=2)
    bridge = CompositeSource(env["bridge"], {"mac": remote})
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.10")
    app = create_app(registry=env["registry"], bridge=bridge, auth=env["auth"], transcriber=None, config=config,
                     agents=tokens, remotes={"mac": remote})
    return LiveHub(app), token, remote


def wait_for(predicate, tries=200):
    for _ in range(tries):
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_agent_session_listed_and_commanded(env, tmp_path):
    import httpx
    hub, token, remote = live_agent_env(env, tmp_path)
    try:
        with hub.agent(token) as ws:
            ws.send(json.dumps({"type": "hello", "agent": "mac", "netbird_ip": "192.0.2.20"}))
            ws.send(json.dumps(MAC_SNAP))
            state = lambda: httpx.get(hub.base + "/api/state").json()
            assert wait_for(lambda: any(s["id"] == "mac-A" for s in state()["sessions"]))
            mac = next(s for s in state()["sessions"] if s["id"] == "mac-A")
            assert mac["offline"] is False and mac["urls"][0]["href"] == "http://192.0.2.20:5173/"
            result = {}
            t = threading.Thread(target=lambda: result.update(
                r=httpx.post(hub.base + "/api/sessions/mac-A/send", json={"text": "run tests"}, timeout=10)))
            t.start()
            cmd = json.loads(ws.recv(timeout=5))
            assert cmd["op"] == "send_text" and cmd["sid"] == "mac-A" and cmd["text"] == "run tests"
            ws.send(json.dumps({"type": "result", "id": cmd["id"], "ok": True}))
            t.join(5)
            assert result["r"].status_code == 200
            ws.send(json.dumps({"type": "hook", "session": "mac-A",
                                "event": {"hook_event_name": "Notification", "message": "perm"}}))
            assert wait_for(lambda: env["registry"].sessions["mac-A"].status.value == "needs_you")
        assert wait_for(lambda: next(s for s in httpx.get(hub.base + "/api/state").json()["sessions"]
                                     if s["id"] == "mac-A")["offline"])
        r = httpx.post(hub.base + "/api/sessions/mac-A/send", json={"text": "x"})
        assert r.status_code == 409 and r.json()["detail"] == "Your Mac is offline."
    finally:
        hub.stop()


def test_start_session_on_mac(env, tmp_path):
    import httpx
    hub, token, remote = live_agent_env(env, tmp_path)
    try:
        with hub.agent(token) as ws:
            ws.send(json.dumps({"type": "hello", "agent": "mac"}))
            ws.send(json.dumps(MAC_SNAP))
            assert wait_for(lambda: bool(remote.projects))
            result = {}
            t = threading.Thread(target=lambda: result.update(
                r=httpx.post(hub.base + "/api/new_session", json={"project": "Atlas", "where": "mac"}, timeout=10)))
            t.start()
            cmd = json.loads(ws.recv(timeout=5))
            assert cmd["op"] == "create_tab" and cmd["project"] == "Atlas" and cmd["command"] == "claude"
            ws.send(json.dumps({"type": "result", "id": cmd["id"], "ok": True, "data": "mac-NEW"}))
            t.join(5)
            assert result["r"].json() == {"id": "mac-NEW"}
        assert wait_for(lambda: not remote.online)
        assert httpx.post(hub.base + "/api/new_session", json={"project": "Atlas", "where": "mac"}).status_code == 409
    finally:
        hub.stop()

def tts_client(env, tmp_path, fetch=None):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects")
    calls = []

    async def fake_fetch(url, text, voice):
        calls.append((url, text, voice))
        return b"ID3-mp3-bytes"
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None,
                     config=config, tts_fetch=fetch or fake_fetch)
    return app, calls


def test_tts_uses_shared_voice_af_heart_by_default(env, tmp_path):
    app, calls = tts_client(env, tmp_path)
    local = TestClient(app, base_url="https://testserver", client=LOCAL)
    r = local.get("/api/tts", params={"text": "Atlas is done."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3-mp3-bytes"
    assert calls[0][1:] == ("Atlas is done.", "af_heart")
    assert local.get("/api/state").json()["tts_voice"] == "af_heart"
    remote = TestClient(app, base_url="https://testserver", client=REMOTE)
    assert remote.get("/api/tts", params={"text": "x"}).status_code == 401


def test_voice_setting_is_shared_and_persisted(env, tmp_path):
    app, calls = tts_client(env, tmp_path)
    local = TestClient(app, base_url="https://testserver", client=LOCAL)
    assert local.post("/api/voice-setting", json={"voice": "bf_emma"}).json() == {"voice": "bf_emma"}
    local.get("/api/tts", params={"text": "hi"})
    assert calls[-1][2] == "bf_emma" and local.get("/api/state").json()["tts_voice"] == "bf_emma"
    assert json.loads((tmp_path / "settings.json").read_text())["tts_voice"] == "bf_emma"
    assert local.post("/api/voice-setting", json={"voice": "../../etc"}).status_code == 400


def test_tts_failure_is_502_and_long_text_refused(env, tmp_path):
    async def down(url, text, voice):
        raise OSError("connection refused")
    app, _ = tts_client(env, tmp_path, fetch=down)
    local = TestClient(app, base_url="https://testserver", client=LOCAL)
    assert local.get("/api/tts", params={"text": "hello"}).status_code == 502
    assert local.get("/api/tts", params={"text": "x" * 1001}).status_code == 400


class ServerBridge(FakeBridge):
    async def create_on_server(self, name, server, project):
        self.on_server = (name, server, project)
        return "mac-W"


class FakeMac:
    def __init__(self, online=True):
        self.online, self.projects = online, []
        self.servers = [{"name": "trading", "online": True, "projects": ["edx"]},
                        {"name": "ledger", "online": False, "projects": ["api"]}]


def server_env(env, tmp_path, online=True):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.10")
    bridge = ServerBridge()
    app = create_app(registry=env["registry"], bridge=bridge, auth=env["auth"], transcriber=None, config=config,
                     remotes={"mac": FakeMac(online)})
    return TestClient(app, base_url="https://testserver", client=LOCAL), bridge


def test_new_session_on_a_server_goes_through_the_mac(env, tmp_path):
    client, bridge = server_env(env, tmp_path)
    r = client.post("/api/new_session", json={"project": "edx", "where": "trading"})
    assert r.status_code == 200 and r.json() == {"id": "mac-W"}
    assert bridge.on_server == ("mac", "trading", "edx")
    assert client.post("/api/new_session", json={"project": "api", "where": "ledger"}).status_code == 409
    assert client.post("/api/new_session", json={"project": "edx", "where": "nowhere"}).status_code == 409
    assert client.post("/api/new_session", json={"project": "nope", "where": "trading"}).status_code == 404
    offline, _ = server_env(env, tmp_path, online=False)
    r = offline.post("/api/new_session", json={"project": "edx", "where": "trading"})
    assert r.status_code == 409 and r.json()["detail"] == "Your Mac is offline."


def test_state_lists_servers_and_each_sessions_server(env, tmp_path):
    client, _ = server_env(env, tmp_path)
    body = client.get("/api/state").json()
    assert body["servers"] == [{"name": "hub", "online": True}, {"name": "trading", "online": True},
                               {"name": "ledger", "online": False}]
    assert body["sessions"][0]["server"] == ""


def test_new_session_waits_for_opencodes_prompt_before_typing_the_task(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.20",
                    agent="opencode")
    (tmp_path / "Projects" / "Beacon").mkdir(parents=True, exist_ok=True)
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"],
                     transcriber=env["transcriber"], config=config, ready_timeout=0.6, ready_settle=0.05)

    async def create_tab(cwd, command):  # OpenCode is running and its plugin reported in, but it is still drawing
        env["registry"].update(list(_snaps(env)) + [Snapshot("NEWID", "opencode", cwd, "opencode", "/dev/ttys9", 9, "opencode")], 1)
        env["registry"].apply_hook("NEWID", {"hook_event_name": "SessionStart"}, 1)
        return "NEWID"
    env["bridge"].create_tab = create_tab
    TestClient(app, base_url="https://testserver", client=LOCAL).post(
        "/api/new_session", json={"project": "Beacon", "task": "fix it"})
    assert env["bridge"].sent == []  # never typed while the prompt isn't there: the keys would be lost


def test_assistant_hint_for_whisper():
    from tabdeck.web import assistant_hint
    assert assistant_hint("Jarvis", "jarvis") == "Jarvis"
    assert assistant_hint("ODS", "hey ods") == 'ODS ("Hey ODS")'


def test_opencode_hub_starts_sessions_through_agent_sh(env, tmp_path):
    (tmp_path / "Projects" / "Beacon").mkdir(parents=True, exist_ok=True)
    config = Config(data_dir=tmp_path / ".tabdeck-ods", projects_dir=tmp_path / "Projects", agent="opencode")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None, config=config)
    TestClient(app, base_url="https://testserver", client=LOCAL).post("/api/new_session", json={"project": "Beacon"})
    assert env["bridge"].created[-1][1] == "~/.tabdeck-ods/agent.sh"


def test_no_server_name_means_the_hub_whatever_it_is_called(env, tmp_path):
    (tmp_path / "Projects" / "Beacon").mkdir(parents=True, exist_ok=True)
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", hub_server="build-box")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None, config=config)
    client = TestClient(app, base_url="https://testserver", client=LOCAL)
    for where in (None, "", "build-box"):
        body = {"project": "Beacon"} if where is None else {"project": "Beacon", "where": where}
        assert client.post("/api/new_session", json=body).status_code == 200
    assert len(env["bridge"].created) == 3  # all started locally on the hub
    assert client.post("/api/new_session", json={"project": "Beacon", "where": "hub1"}).status_code == 409  # not special any more


def settings_client(env, tmp_path):
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", netbird_ip="192.0.2.20")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None, config=config)
    return TestClient(app, base_url="https://testserver", client=LOCAL)


def test_settings_change_applies_live_without_a_restart(env, tmp_path):
    client = settings_client(env, tmp_path)
    assert client.get("/api/settings").json()["values"]["wake_word"] == "jarvis"
    r = client.post("/api/settings", json={"assistant_name": "Friday", "wake_word": "hey friday"})
    assert r.status_code == 200 and r.json()["values"]["assistant_name"] == "Friday"
    said = lambda text: client.post("/api/utterance", json={"text": text, "wake": "1"}).json()
    assert said("Hey Friday, what's going on?")["heard"] is True
    assert said("Jarvis, what's going on?")["heard"] is False  # the old wake word no longer works
    state = client.get("/api/state").json()
    assert (state["assistant_name"], state["wake_phrase"]) == ("Friday", "Hey Friday")
    assert json.loads((tmp_path / "settings.json").read_text())["wake_word"] == "hey friday"  # kept after restarts


def test_bad_settings_are_refused_whole_and_explained(env, tmp_path):
    client = settings_client(env, tmp_path)
    r = client.post("/api/settings", json={"wake_word": "hey friday", "tts_url": "http://8.8.8.8:8880", "port": 1})
    assert r.status_code == 400
    assert len(r.json()["detail"]["errors"]) == 2
    assert client.get("/api/settings").json()["values"]["wake_word"] == "jarvis"  # nothing half-applied
    assert not (tmp_path / "settings.json").exists()


def test_reload_reads_settings_json_again(env, tmp_path):
    client = settings_client(env, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"assistant_name": "Nova", "wake_word": "nova", "port": 9999}))
    body = client.post("/api/settings/reload").json()
    assert body["values"]["assistant_name"] == "Nova"
    assert body["restart_needed"] == ["port"]  # read-only values only change after a restart
    assert client.post("/api/utterance", json={"text": "Nova, status", "wake": "1"}).json()["heard"] is True


def test_wake_word_test_and_settings_need_pairing(env, tmp_path):
    client = settings_client(env, tmp_path)
    assert client.post("/api/settings/test-wake", json={"text": "Hey, Jarvis. Status"}).json() == {
        "matches": True, "rest": "Status"}
    assert client.post("/api/settings/test-wake", json={"text": "hello there"}).json()["matches"] is False
    assert env["remote"].get("/api/settings").status_code == 401
    assert env["remote"].post("/api/settings", json={"wake_word": "x"}).status_code == 401
    assert env["remote"].post("/api/settings/reload").status_code == 401


def test_voice_list_comes_from_the_hubs_voice_server(env, tmp_path):
    async def voices(url):
        assert url == "http://100.64.0.5:8880"
        return ["af_heart", "am_michael", "bf_emma", "zf_xiaobei", "af_heart_v0"]
    config = Config(data_dir=tmp_path, projects_dir=tmp_path / "Projects", tts_url="http://100.64.0.5:8880")
    app = create_app(registry=env["registry"], bridge=env["bridge"], auth=env["auth"], transcriber=None, config=config,
                     tts_voices=voices)
    client = TestClient(app, base_url="https://testserver", client=LOCAL)
    assert client.get("/api/tts/voices").json() == {"voices": ["af_heart", "am_michael", "bf_emma"]}  # English only
    assert env["remote"].get("/api/tts/voices").status_code == 401


def test_voice_list_is_empty_without_a_voice_server(env, tmp_path):
    client = settings_client(env, tmp_path)
    assert client.get("/api/tts/voices").json() == {"voices": []}


def test_reply_keeps_its_lines_for_display(env, tmp_path):
    import json as _json
    t = tmp_path / "t.jsonl"
    reply = "Done. Changes:\n\n- **auth.py**: fixed the token check\n- tests: 327 passed\n\n```\nuv run pytest\n```"
    t.write_text(_json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}) + "\n")
    env["registry"].sessions["A"].transcript_path = str(t)
    body = env["local"].get("/api/sessions/A/reply").json()
    assert body["text"] == reply  # shown as written, line breaks and all
    assert body["chunks"] and "**" not in " ".join(body["chunks"])  # read aloud without markdown


def test_page_and_static_files_are_revalidated_after_a_deploy(env):
    for path in ("/", "/static/style.css", "/static/app.js"):
        r = env["local"].get(path)
        assert r.status_code == 200 and r.headers["cache-control"] in ("no-cache", "no-store"), path  # new versions show at once


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "CF-Connecting-IP"])
def test_requests_through_a_proxy_on_the_hub_are_never_trusted_as_local(env, header):
    # nginx, Caddy or a tunnel on the same machine connect from 127.0.0.1; the internet must still pair.
    h = {header: "203.0.113.7"}
    assert env["local"].get("/api/state", headers=h).status_code == 401
    assert env["local"].post("/api/pair", headers=h).status_code == 403
    assert env["local"].post("/hook", json={}, headers=h).status_code == 403
    assert env["local"].get("/api/state").status_code == 200  # a direct local request still works
