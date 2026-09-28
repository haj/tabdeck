import json

from tabdeck.transcript import last_assistant_text


def write(tmp_path, entries):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(p)


def a(*texts, tool=False):
    content = [{"type": "text", "text": t} for t in texts]
    if tool:
        content.append({"type": "tool_use", "id": "x", "name": "Bash", "input": {}})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def u(text=None, tool_result=False):
    if tool_result:
        return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
    return {"type": "user", "message": {"content": text}}


def test_final_text_after_tools(tmp_path):
    path = write(tmp_path, [u("fix it"), a("Let me look.", tool=True), u(tool_result=True),
                            a("Fixed the bug."), a("All tests pass.")])
    assert last_assistant_text(path) == "Fixed the bug.\n\nAll tests pass."


def test_skips_bad_lines_and_non_message_entries(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('not json\n' + json.dumps(u("hi")) + "\n" + json.dumps(a("Hello.")) + "\n"
                 + json.dumps({"type": "summary", "summary": "x"}) + "\n")
    assert last_assistant_text(str(p)) == "Hello."


def test_missing_file():
    assert last_assistant_text("/nonexistent/file.jsonl") == ""


def test_does_not_return_an_older_turn_when_latest_is_not_written_yet(tmp_path):
    path = write(tmp_path, [u("first question"), a("Old answer."), u("second question")])
    assert last_assistant_text(path) == ""


def test_turn_still_running_tools_returns_nothing_yet(tmp_path):
    path = write(tmp_path, [u("q1"), a("Old answer."), u("q2"), a(tool=True), u(tool_result=True)])
    assert last_assistant_text(path) == ""


def test_latest_turn_text_is_returned(tmp_path):
    path = write(tmp_path, [u("q1"), a("Old answer."), u("q2"), a("Looking.", tool=True), u(tool_result=True),
                            a("New answer.")])
    assert last_assistant_text(path) == "New answer."
