import pytest

from tabdeck.profiles import AGENTS, agent_profile, instance_suffix


def test_claude_and_opencode_profiles():
    c, o = agent_profile("claude"), agent_profile("opencode")
    assert (c.name, c.process, c.launch("~/.tabdeck"), c.ready_text) == ("Claude", "claude", "claude", "")
    assert (o.name, o.process, o.ready_text) == ("OpenCode", "opencode", "Ask anything")
    assert o.launch("~/.tabdeck-ods") == "~/.tabdeck-ods/agent.sh"  # wrapper: ODS model + status plugin
    assert c.is_agent("claude") and not c.is_agent("zsh") and o.is_agent("opencode")
    assert set(AGENTS) == {"claude", "opencode"}


def test_unknown_agent_is_refused():
    with pytest.raises(ValueError):
        agent_profile("vim")


def test_instance_suffix(monkeypatch):
    monkeypatch.delenv("TABDECK_INSTANCE", raising=False)
    assert instance_suffix() == ""
    monkeypatch.setenv("TABDECK_INSTANCE", "ods")
    assert instance_suffix() == "-ods"
    monkeypatch.setenv("TABDECK_INSTANCE", "../evil")
    with pytest.raises(ValueError):
        instance_suffix()
