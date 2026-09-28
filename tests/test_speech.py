from tabdeck.speech import chunk, clean_for_speech, describe, display_names, summary
from tabdeck.status import SessionState, Status, set_status


def test_clean_removes_code_links_and_markdown():
    md = "## Done\n\nI fixed `session_id` handling.\n\n```python\nprint(1)\n```\n- first item\n- second\n\nSee [docs](https://x.io) or https://y.io/page."
    out = clean_for_speech(md)
    assert "print" not in out and "```" not in out and "#" not in out
    assert "session id" in out
    assert "(code omitted)" in out
    assert "docs" in out and "https" not in out
    assert "first item." in out


def test_clean_drops_tables():
    assert clean_for_speech("Result:\n| a | b |\n|---|---|\n| 1 | 2 |\nEnd.") == "Result: End."


def test_chunk_groups_sentences():
    assert chunk("One. Two! Three? Four.", 3) == ["One. Two! Three?", "Four."]
    assert chunk("", 3) == []


def _st(sid, cwd, status=Status.IDLE, message=""):
    st = SessionState(session_id=sid, cwd=cwd)
    set_status(st, status, 1)
    st.message = message
    return st


def test_display_names_dedupes():
    names = display_names([_st("a", "/p/Atlas"), _st("b", "/p/Atlas"), _st("c", "/p/Beacon")])
    assert names == {"a": "Atlas", "b": "Atlas 2", "c": "Beacon"}


def test_describe():
    st = _st("a", "/p/Atlas", Status.NEEDS_YOU, "Claude needs your permission to use Bash")
    assert describe(st, "Atlas") == "Atlas needs you. Claude needs your permission to use Bash"


def test_summary():
    ss = [_st("a", "/p/Atlas", Status.NEEDS_YOU, "Permission for Bash"),
          _st("b", "/p/Beacon", Status.DONE, "Login page done."),
          _st("c", "/p/Umnia", Status.WORKING)]
    names = display_names(ss)
    text = summary(ss, names)
    assert text.startswith("1 needs you.")
    assert "Atlas needs you. Permission for Bash" in text
    assert "Beacon is done. Login page done." in text
    assert "Working: Umnia." in text


def test_summary_all_quiet():
    assert summary([_st("a", "/p/x")], {"a": "x"}) == "All quiet. Nothing needs you."


def test_describe_permission_asks_to_approve():
    st = _st("a", "/p/Atlas", Status.NEEDS_YOU, "wants to run: npm install stripe")
    assert describe(st, "Atlas") == "Atlas wants to run: npm install stripe. Approve?"
