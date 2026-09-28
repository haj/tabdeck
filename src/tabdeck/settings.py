"""Settings that can change while TabDeck runs (from the widget or the web page), how each is checked, and
how they are saved. Deploy-time settings (agent, port, tmux session) are shown but changed with `tabdeck setup`."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config
from .files import write_private

NAME = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,29}$")
VOICE = re.compile(r"^[a-z]{2}_[a-z0-9_]{1,30}$")
MODEL = re.compile(r"^[A-Za-z0-9._:/@-]{1,100}$")
KEY = re.compile(r"^[\x21-\x7e]{0,300}$")  # printable, no spaces
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # NetBird, Tailscale

# key -> (group, label) for the widget and web page; "llm_url" is stored as the Config field ollama_url.
EDITABLE = {
    "assistant_name": ("Assistant", "Name"),
    "wake_word": ("Assistant", "Wake word"),
    "tts_voice": ("Assistant", "Voice"),
    "llm_api": ("Model", "API (ollama or openai)"),
    "llm_url": ("Model", "Server URL"),
    "intent_model": ("Model", "Model"),
    "llm_key": ("Model", "API key"),
    "intent_timeout": ("Model", "Answer timeout (s)"),
    "summary_timeout": ("Model", "Summary timeout (s)"),
    "stt_url": ("Speech", "Speech-to-text URL (empty: Whisper on the Mac)"),
    "stt_model": ("Speech", "Speech-to-text model"),
    "tts_url": ("Speech", "Voice server URL (empty: system voice)"),
}
READ_ONLY = ("agent", "port", "tmux_session", "hub_server", "source")


def _url(value: str, what: str, required: bool) -> tuple[str | None, str | None]:
    v = value.strip().rstrip("/")
    if not v:
        return (None, f"{what}: a URL is needed") if required else ("", None)
    u = urlsplit(v)
    if u.scheme not in ("http", "https") or not u.hostname:
        return None, f"{what}: use an http:// or https:// URL"
    if u.username or u.password:
        return None, f"{what}: no user name or password in the URL (use the API key field)"
    if u.query or u.fragment:
        return None, f"{what}: no ?query or #fragment"
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        return v, None  # a host name (e.g. on the VPN's DNS): can't be checked here
    if not (ip.is_private or ip.is_loopback or ip in CGNAT):
        return None, f"{what}: {ip} is a public address; model and speech servers must be on your private network"
    return v, None


def check(changes: dict) -> tuple[dict, list[str]]:
    """Validate a partial update. Returns the cleaned values and one message per refused key."""
    clean, errors = {}, []
    for key, raw in changes.items():
        if key in READ_ONLY:
            errors.append(f"{key} is set when deploying: run `tabdeck setup`")
            continue
        if key not in EDITABLE:
            errors.append(f"unknown setting {key!r}")
            continue
        value = raw.strip() if isinstance(raw, str) else raw
        if key in ("assistant_name", "wake_word"):
            text = " ".join(str(value or "").split())
            what = "assistant name" if key == "assistant_name" else "wake word"
            if not NAME.match(text) or len(text.split()) > 3:
                errors.append(f"{what}: one to three words of letters")
                continue
            clean[key] = text.lower() if key == "wake_word" else text
        elif key == "tts_voice":
            if not VOICE.match(str(value)):
                errors.append("voice: a Kokoro voice name such as af_heart")
                continue
            clean[key] = value
        elif key in ("tts_url", "stt_url", "llm_url"):
            what = {"tts_url": "voice server", "stt_url": "speech-to-text server", "llm_url": "model server"}[key]
            v, err = _url(str(value or ""), what, required=key == "llm_url")
            if err:
                errors.append(err)
                continue
            clean[key] = v
        elif key == "llm_api":
            if value not in ("ollama", "openai"):
                errors.append("API: ollama or openai")
                continue
            clean[key] = value
        elif key in ("intent_model", "stt_model"):
            if not MODEL.match(str(value)):
                errors.append(f"model name {value!r}: letters, digits and . _ : / @ -")
                continue
            clean[key] = value
        elif key == "llm_key":
            if not KEY.match(str(value)):
                errors.append("API key: printable characters without spaces")
                continue
            clean[key] = value
        elif key in ("intent_timeout", "summary_timeout"):
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                seconds = -1
            if not 1 <= seconds <= 300:
                errors.append(f"{key.replace('_', ' ')}: between 1 and 300 seconds")
                continue
            clean[key] = seconds
    return clean, errors


def view(config: Config) -> dict:
    """What the settings screens show: current values (the API key only as set/empty), labels, read-only values."""
    values = {k: getattr(config, "ollama_url" if k == "llm_url" else k) for k in EDITABLE}
    values["llm_key"] = "set" if config.llm_key else ""
    return {"values": values, "fields": [{"key": k, "group": g, "label": lbl} for k, (g, lbl) in EDITABLE.items()],
            "read_only": {k: getattr(config, k) for k in READ_ONLY}}


def save(data_dir: Path, clean: dict) -> None:
    path = Path(data_dir) / "settings.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data.update(clean)
    write_private(path, json.dumps(data, indent=2) + "\n")


def apply(config: Config, clean: dict) -> Config:
    fields = {("ollama_url" if k == "llm_url" else k): v for k, v in clean.items()}
    return replace(config, **fields)
