"""What varies between TabDeck setups: the instance (so several can run side by side) and the coding
agent each session runs (Claude Code or OpenCode)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

INSTANCE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def instance_suffix() -> str:
    """"" for the default instance, "-ods" for TABDECK_INSTANCE=ods: data folder ~/.tabdeck-ods,
    launchd jobs com.tabdeck-ods.*, cron marker "# tabdeck-ods"."""
    name = os.environ.get("TABDECK_INSTANCE", "").strip()
    if not name:
        return ""
    if not INSTANCE_NAME.match(name):
        raise ValueError(f"invalid TABDECK_INSTANCE {name!r}: lowercase letters, digits and dashes")
    return "-" + name


@dataclass(frozen=True)
class AgentProfile:
    key: str
    name: str  # what the assistant calls it
    process: str  # its process name, as tmux and ps report it
    wrapper: bool  # started through <data dir>/agent.sh (sets its config, e.g. ODS's model and plugin)
    ready_text: str  # on screen once its prompt accepts typing ("" = running is enough)
    status: str  # how it reports status: "claude-hooks" or "opencode-plugin"
    product: str  # its full name in the model's prompts, e.g. "Claude Code"

    def launch(self, home: str) -> str:
        """The command a new session runs. `home` is the data folder as the shell sees it, e.g. ~/.tabdeck."""
        return f"{home}/agent.sh" if self.wrapper else self.process

    def is_agent(self, job: str) -> bool:
        return job == self.process


AGENTS = {
    "claude": AgentProfile("claude", "Claude", "claude", False, "", "claude-hooks", "Claude Code"),
    "opencode": AgentProfile("opencode", "OpenCode", "opencode", True, "Ask anything", "opencode-plugin", "OpenCode"),
}


def agent_profile(key: str) -> AgentProfile:
    try:
        return AGENTS[key]
    except KeyError:
        raise ValueError(f"unknown agent {key!r}; choose one of {', '.join(AGENTS)}") from None
