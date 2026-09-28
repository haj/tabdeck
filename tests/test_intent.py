import json

from tabdeck.intent import Interpreter, llm_action, system_prompt
from tabdeck.speech import display_names
from tabdeck.status import SessionState, Status, set_status


def mk(sid, cwd, status=Status.IDLE, message="", urls=()):
    s = SessionState(session_id=sid, cwd=cwd)
    set_status(s, status, 1)
    s.message, s.urls = message, list(urls)
    return s


def world():
    ss = [mk("f", "/p/Atlas", Status.NEEDS_YOU, "Claude needs your permission to use Bash " + "x" * 400),
          mk("s", "/p/Orbit", Status.WORKING), mk("g", "/p/garden", urls=["http://localhost:5173/"])]
    return ss, display_names(ss)


def test_system_prompt_lists_tabs_as_quoted_capped_data():
    ss, names = world()
    p = system_prompt(ss, names, "g")
    assert "- Atlas: needs_you" in p and "- Orbit: working" in p
    assert 'says: "Claude needs your permission to use Bash' in p
    assert "x" * 301 not in p
    assert "Selected tab: garden" in p


def test_llm_action_mapping():
    ss, names = world()
    act = lambda d, sel="g": llm_action(d, ss, names, sel, ["Beacon"])
    assert act({"action": "none"}) is None
    assert act({"action": "approve", "tab": "Atlas"}) == {
        "type": "keys", "session_id": "f", "key": "enter", "speak": "Approved."}
    assert act({"action": "approve", "tab": "Orbit"})["type"] == "ask"
    assert act({"action": "reply", "tab": "orbit", "text": "push and open a PR"}) == {
        "type": "reply", "session_id": "s", "text": "push and open a PR", "speak": "Sending to Orbit."}
    assert act({"action": "reply", "text": "add dark mode"})["session_id"] == "g"
    assert act({"action": "reply", "tab": "garden", "text": "  "})["type"] == "ask"
    assert act({"action": "select", "tab": "Orbit"})["session_id"] == "s"
    assert act({"action": "status"})["type"] == "speak"
    assert act({"action": "open_app"}) == {"type": "open_url", "session_id": "g", "index": 0}
    assert act({"action": "new_session", "project": "beacon"})["project"] == "Beacon"
    assert act({"action": "read", "tab": "banana"}) == {"type": "ask", "text": "I couldn't find a tab called banana."}
    assert act({"action": "rm -rf"})["type"] == "ask"
    assert act({"action": "cancel"}) == {"type": "cancel"}


def test_interpreter_parses_json_and_survives_errors():
    ss, names = world()
    seen = {}

    def post(body):
        seen["body"] = body
        return json.dumps({"message": {"content": '{"action": "status"}'}})

    it = Interpreter(post=post)
    assert it.interpret("what's going on", ss, names, "g") == {"action": "status"}
    assert seen["body"]["format"] == "json" and seen["body"]["messages"][-1]["content"] == "what's going on"

    def boom(body):
        raise TimeoutError("slow")

    assert Interpreter(post=boom).interpret("x", ss, names, None) is None
    assert Interpreter(post=lambda b: json.dumps({"message": {"content": "not json"}})).interpret("x", ss, names, None) is None
    assert Interpreter(post=lambda b: json.dumps({"message": {"content": "[1]"}})).interpret("x", ss, names, None) is None


def test_warm_uses_long_timeout_and_keeps_model_loaded():
    calls = []
    it = Interpreter(post=lambda b: calls.append(("short", b)) or "{}",
                     warm_post=lambda b: calls.append(("long", b)) or "{}")
    it.warm()
    assert calls[0][0] == "long" and calls[0][1]["keep_alive"] == -1


def test_compose_asks_for_the_message():
    ss, names = world()
    assert llm_action({"action": "compose", "tab": "Atlas"}, ss, names, "g", []) == {
        "type": "compose", "session_id": "f", "speak": "What should I send to Atlas?"}
    assert llm_action({"action": "compose"}, ss, names, None, [])["type"] == "ask"


def test_prompt_explains_bare_instructions_and_compose():
    ss, names = world()
    p = system_prompt(ss, names, "g")
    assert '"compose"' in p and "no tab named" in p and "pending" in p


def test_tab_the_user_did_not_mention_is_ignored():
    ss, names = world()
    guess = {"action": "reply", "tab": "Atlas", "text": "run the tests"}
    assert llm_action(guess, ss, names, "g", [], utterance="run the tests")["session_id"] == "g"
    assert llm_action(guess, ss, names, "g", [], utterance="tell atlas to run the tests")["session_id"] == "f"
    shen = {"action": "reply", "tab": "Orbit", "text": "push"}
    assert llm_action(shen, ss, names, "g", [], utterance="tell the orbit one to push")["session_id"] == "s"


def test_polish_parses_and_survives_errors():
    seen = {}

    def post(body):
        seen["body"] = body
        return json.dumps({"message": {"content": json.dumps(
            {"message": "Run the tests and fix failures.", "confident": True, "question": ""})}})

    it = Interpreter(post=post)
    out = it.polish("um run the uh tests and fix fail failures", "Atlas", "Tests pass.")
    assert out == {"message": "Run the tests and fix failures.", "confident": True, "question": ""}
    system = seen["body"]["messages"][0]["content"]
    assert "Atlas" in system and '"Tests pass."' in system
    assert seen["body"]["messages"][-1]["content"] == "um run the uh tests and fix fail failures"
    assert Interpreter(post=lambda b: (_ for _ in ()).throw(TimeoutError())).polish("x", "Atlas", "") is None
    bad = Interpreter(post=lambda b: json.dumps({"message": {"content": '{"confident": true}'}}))
    assert bad.polish("x", "Atlas", "") is None


def test_answer_action_speaks_the_models_answer():
    ss, names = world()
    assert llm_action({"action": "answer", "text": "Atlas is waiting for permission to run Bash."}, ss, names, "g", []) == {
        "type": "speak", "text": "Atlas is waiting for permission to run Bash."}
    assert llm_action({"action": "answer", "text": " "}, ss, names, "g", [])["type"] == "ask"
    assert '"answer"' in system_prompt(ss, names, "g")


def test_answer_length_setting_changes_the_instruction():
    ss, names = world()
    short, normal, detailed = (system_prompt(ss, names, "g", length=n) for n in ("short", "normal", "detailed"))
    assert "one short sentence" in short
    assert "two to four" in normal
    assert "four to six" in detailed
    assert system_prompt(ss, names, "g", length="bogus") == normal


def test_summarize_returns_spoken_summary():
    seen = {}

    def post(body):
        seen["body"] = body
        return json.dumps({"message": {"content": json.dumps({"summary": "Added the login page. Tests pass."})}})

    out = Interpreter(post=post).summarize("## Done\nI added `login.py`...", "Atlas")
    assert out == "Added the login page. Tests pass."
    assert "Atlas" in seen["body"]["messages"][0]["content"]
    assert Interpreter(post=lambda b: (_ for _ in ()).throw(TimeoutError())).summarize("x", "Atlas") is None


def test_llm_new_session_with_task():
    ss, names = world()
    act = llm_action({"action": "new_session", "project": "beacon", "task": "fix the tests"}, ss, names, None, ["Beacon"])
    assert act["project"] == "Beacon" and act["task"] == "fix the tests"


def test_new_session_on_a_known_server():
    ss, names = world()
    act = llm_action({"action": "new_session", "project": "beacon", "where": "Trading"}, ss, names, None, ["Beacon"],
                     servers=["trading"])
    assert act["where"] == "trading" and "on trading" in act["speak"]
    act = llm_action({"action": "new_session", "project": "beacon", "where": "nowhere"}, ss, names, None, ["Beacon"],
                     servers=["trading"])
    assert "where" not in act
    act = llm_action({"action": "new_session", "project": "beacon", "where": "mac"}, ss, names, None, ["Beacon"])
    assert act["where"] == "mac" and "on your Mac" in act["speak"]


def test_prompt_names_servers_and_where_sessions_run():
    ss, names = world()
    ss[1].server = "trading"
    p = system_prompt(ss, names, None, servers=["hub1", "trading"])
    assert "- Orbit (on trading): working" in p and "Servers: hub1, trading" in p


def test_openai_mode_talks_to_ods_litellm():
    ss, names = world()
    seen = {}

    def post(body):
        seen["body"] = body
        return json.dumps({"choices": [{"message": {"content": '{"action": "status"}'}}]})
    it = Interpreter(url="http://ods:4000/v1", model="default", api="openai", post=post)
    assert it.interpret("what's going on", ss, names, None) == {"action": "status"}
    body = seen["body"]
    assert body["model"] == "default" and body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0 and body["stream"] is False
    assert "keep_alive" not in body and "format" not in body  # Ollama-only fields
    assert it.endpoint == "http://ods:4000/v1/chat/completions"


def test_openai_mode_sends_the_key():
    from tabdeck.intent import _http_post
    import urllib.request
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout):
        captured["auth"] = req.get_header("Authorization")
        return Resp()
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _http_post("http://ods:4000/v1/chat/completions", 5, key="sk-test")({"a": 1})
    finally:
        urllib.request.urlopen = orig
    assert captured["auth"] == "Bearer sk-test"


def test_configure_switches_server_and_model_live():
    it = Interpreter(url="http://localhost:11434", model="gemma4:latest")
    it.configure(url="http://10.0.0.2:4000/v1", model="default", api="openai", key="k", timeout=20,
                 summary_timeout=60, assistant="Friday")
    assert (it.endpoint, it.model, it.api, it.assistant) == ("http://10.0.0.2:4000/v1/chat/completions", "default",
                                                              "openai", "Friday")
    assert "response_format" in it._body([])  # OpenAI-style requests from now on
