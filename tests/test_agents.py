import stat

from tabdeck.agents import AgentTokens


def test_issue_and_verify(tmp_path):
    f = tmp_path / "agents.json"
    token = AgentTokens(f).issue("mac")
    assert AgentTokens(f).verify(token) == "mac"
    assert AgentTokens(f).verify("wrong") is None and AgentTokens(f).verify("") is None
    assert token not in f.read_text() and stat.S_IMODE(f.stat().st_mode) == 0o600


def test_reissue_replaces_old_token(tmp_path):
    t = AgentTokens(tmp_path / "a.json")
    old = t.issue("mac")
    new = t.issue("mac")
    assert t.verify(old) is None and t.verify(new) == "mac"


def test_write_private_creates_owner_only_file_from_the_start(tmp_path, monkeypatch):
    import os
    from tabdeck.files import write_private
    opened = {}
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        opened["mode"] = mode
        return real_open(path, flags, mode, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    p = tmp_path / "secret.json"
    write_private(p, "token")
    assert opened["mode"] == 0o600 and p.read_text() == "token"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
