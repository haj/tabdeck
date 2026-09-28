import json

import pytest

from tabdeck.config import Config
from tabdeck.settings import apply, check, save, view


def test_check_accepts_good_values_and_normalises():
    clean, errors = check({"assistant_name": " Friday ", "wake_word": "Hey  Friday", "tts_voice": "af_heart",
                           "tts_url": "http://100.64.0.5:8880/", "stt_url": "", "llm_api": "openai",
                           "llm_url": "https://models.lan/v1", "intent_model": "default", "intent_timeout": "15",
                           "llm_key": "sk-local-123"})
    assert errors == []
    assert clean["assistant_name"] == "Friday" and clean["wake_word"] == "hey friday"
    assert clean["tts_url"] == "http://100.64.0.5:8880" and clean["stt_url"] == ""
    assert clean["intent_timeout"] == 15.0


@pytest.mark.parametrize("key,value,words", [
    ("wake_word", "", "wake word"),
    ("wake_word", "one two three four", "wake word"),
    ("assistant_name", "<script>", "assistant name"),
    ("tts_voice", "Heart", "voice"),
    ("tts_url", "ftp://100.64.0.5", "http"),
    ("tts_url", "http://8.8.8.8:8880", "private network"),  # a public IP: speech and prompts would leave home
    ("llm_url", "http://user:pw@10.0.0.2/v1", "password"),
    ("llm_url", "", "model server"),
    ("llm_api", "grpc", "ollama or openai"),
    ("intent_timeout", "0", "between"),
    ("intent_model", "bad model!", "model name"),
    ("port", 9000, "tabdeck setup"),  # deploy-time: changing it needs a redeploy
    ("nonsense", 1, "unknown"),
])
def test_check_refuses_bad_values_with_a_clear_reason(key, value, words):
    clean, errors = check({key: value})
    assert key not in clean and len(errors) == 1 and words in errors[0]


def test_view_masks_the_key_and_lists_read_only_values(tmp_path):
    c = Config(data_dir=tmp_path, llm_key="sk-secret", agent="opencode", tmux_session="ods", hub_server="box")
    v = view(c)
    assert v["values"]["llm_key"] == "set" and "sk-secret" not in json.dumps(v)
    assert v["read_only"] == {"agent": "opencode", "port": 8765, "tmux_session": "ods", "hub_server": "box",
                              "source": c.source}
    assert v["values"]["assistant_name"] == "Jarvis" and v["values"]["llm_url"] == c.ollama_url


def test_save_merges_into_settings_json_owner_only(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"agent_token": "t", "wake_word": "jarvis"}))
    save(tmp_path, {"wake_word": "hey friday", "llm_url": "http://10.0.0.2:4000/v1"})
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data == {"agent_token": "t", "wake_word": "hey friday", "llm_url": "http://10.0.0.2:4000/v1"}
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_apply_gives_a_new_config():
    c = Config(assistant_name="Jarvis")
    new = apply(c, {"assistant_name": "Friday", "llm_url": "http://10.0.0.2:4000/v1", "llm_key": ""})
    assert (new.assistant_name, new.ollama_url, new.llm_key) == ("Friday", "http://10.0.0.2:4000/v1", "")
    assert c.assistant_name == "Jarvis"  # the old one is untouched
