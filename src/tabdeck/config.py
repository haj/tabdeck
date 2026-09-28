from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .profiles import AgentProfile, agent_profile, instance_suffix

NETBIRD_IP_RE = re.compile(r"NetBird IP:\s*(\d+\.\d+\.\d+\.\d+)")


def detect_netbird_ip(status_output: str) -> str | None:
    m = NETBIRD_IP_RE.search(status_output)
    return m.group(1) if m else None


@dataclass(frozen=True)
class Config:
    port: int = 8765
    data_dir: Path = field(default_factory=lambda: Path.home() / f".tabdeck{instance_suffix()}")
    projects_dir: Path = field(default_factory=lambda: Path.home() / "Projects")
    netbird_ip: str | None = None
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    ollama_url: str = field(default_factory=lambda: os.environ.get("TABDECK_OLLAMA_URL", "http://localhost:11434"))
    intent_model: str = field(default_factory=lambda: os.environ.get("TABDECK_INTENT_MODEL", "gemma4:latest"))
    source: str = field(default_factory=lambda: "iterm" if sys.platform == "darwin" else "tmux")
    settings: dict = field(default_factory=dict, compare=False)  # the raw settings.json
    tts_url: str = ""  # Kokoro (OpenAI-compatible speech), from settings.json; empty: clients use their own voice
    tts_voice: str = "af_heart"  # the one voice shared by the widget and the web page
    hub_server: str = "hub"  # the name of the server the hub runs on (its tmux sessions are that server's)
    agent: str = "claude"  # the coding agent sessions run: "claude" or "opencode" (profiles.py)
    assistant_name: str = "Jarvis"  # what the voice assistant is called
    wake_word: str = "jarvis"  # "jarvis", "hey ods", or any word or two (commands.wake_pattern)
    tmux_session: str = "deck"  # the tmux session holding one window per agent session on servers
    # The model behind the assistant: Ollama (/api/chat) or an OpenAI-compatible server such as ODS's LiteLLM.
    llm_api: str = "ollama"  # "ollama" or "openai" (url ending in /v1)
    llm_key: str = ""
    intent_timeout: float = 3.0  # a large model on a small GPU needs longer (ODS: ~20 s)
    summary_timeout: float = 8.0
    stt_url: str = ""  # speech-to-text service (OpenAI-compatible, e.g. ODS Whisper); empty: Whisper on the Mac
    stt_model: str = "Systran/faster-whisper-base"
    mac_tabs: bool = True  # Mac agent: report the Mac's own iTerm tabs (off: only server tabs)
    allow_public: bool = False  # hub: also listen on a public address (not recommended; see SECURITY.md)

    @property
    def agent_profile(self) -> AgentProfile:
        return agent_profile(self.agent)

    @property
    def home(self) -> str:
        """The data folder as a shell on any machine of this instance sees it, e.g. ~/.tabdeck-ods."""
        return "~/" + self.data_dir.name

    @property
    def cert_file(self) -> Path:
        return self.data_dir / "cert.pem"

    @property
    def key_file(self) -> Path:
        return self.data_dir / "key.pem"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def tokens_file(self) -> Path:
        return self.data_dir / "tokens.json"

    @property
    def projects_file(self) -> Path:
        return self.data_dir / "projects.json"

    @property
    def hook_file(self) -> Path:
        return self.data_dir / "hook.sh"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "service.log"


def load_config(data_dir: Path | None = None) -> Config:
    """Defaults, overridden by <data dir>/settings.json (agent, llm_url, intent_model, stt_url, tts_url, …).
    The data dir is ~/.tabdeck, or ~/.tabdeck-<name> for TABDECK_INSTANCE=<name>."""
    data_dir = data_dir or Path.home() / f".tabdeck{instance_suffix()}"
    settings: dict = {}
    try:
        settings = json.loads((data_dir / "settings.json").read_text())
    except (OSError, ValueError):
        pass
    ip = os.environ.get("TABDECK_NETBIRD_IP")
    if not ip:
        try:
            out = subprocess.run(
                ["netbird", "status"], capture_output=True, text=True, timeout=5
            ).stdout
            ip = detect_netbird_ip(out)
        except (OSError, subprocess.TimeoutExpired):
            ip = None
    extra = {k: settings[k] for k in ("ollama_url", "intent_model", "source", "tts_url", "tts_voice",
                                         "hub_server", "llm_api", "llm_key", "stt_url", "stt_model", "agent",
                                         "assistant_name", "wake_word", "tmux_session")
             if isinstance(settings.get(k), str)}
    if isinstance(settings.get("llm_url"), str):
        extra["ollama_url"] = settings["llm_url"]  # ODS name for the same setting
    if isinstance(settings.get("projects_dir"), str) and settings["projects_dir"].strip():
        extra["projects_dir"] = Path(settings["projects_dir"]).expanduser()
    for flag in ("mac_tabs", "allow_public"):
        if isinstance(settings.get(flag), bool):
            extra[flag] = settings[flag]
    extra.update({k: float(settings[k]) for k in ("intent_timeout", "summary_timeout")
                  if isinstance(settings.get(k), (int, float))})
    agent_profile(extra.get("agent", "claude"))  # fail early on an unknown agent
    port = int(os.environ.get("TABDECK_PORT") or settings.get("port") or 8765)
    return Config(port=port, netbird_ip=ip, data_dir=data_dir, settings=settings, **extra)


def list_projects(projects_dir: Path) -> list[str]:
    try:
        dirs = [p for p in projects_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return []
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in dirs]
