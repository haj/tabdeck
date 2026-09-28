from __future__ import annotations

import os
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    NEEDS_YOU = "needs_you"
    WORKING = "working"
    DONE = "done"
    IDLE = "idle"


SHELLS = {"zsh", "-zsh", "bash", "-bash", "fish", "sh", "login"}
WORKING_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}


@dataclass
class SessionState:
    session_id: str
    tab_title: str = ""
    cwd: str = ""
    job_name: str = ""
    tty: str = ""
    shell_pid: int = 0
    status: Status = Status.IDLE
    status_since: float = 0.0
    hook_seen: bool = False
    message: str = ""
    transcript_path: str | None = None
    seen: bool = True
    screen_text: str = ""
    screen_changed_at: float = 0.0
    screen_urls: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    pending_tool: str = ""  # what Claude is about to do, e.g. "run: npm test" (from PreToolUse)
    offline: bool = False
    url_host: str | None = None
    remote: bool = False
    server: str = ""  # the server a tmux session runs on; "" for a Mac tab
    pane: str = ""  # tmux pane id on that server

    @property
    def project(self) -> str:
        return os.path.basename(self.cwd.rstrip("/")) or self.tab_title or "shell"


MAX_TOOL = 120


def describe_tool(name: str, tool_input: dict | None) -> str:
    """Short spoken description of a tool call, for permission prompts."""
    inp = tool_input or {}
    if name == "Bash" and inp.get("command"):
        cmd = " ".join(str(inp["command"]).split())
        return "run: " + (cmd if len(cmd) <= MAX_TOOL else cmd[:MAX_TOOL] + "…")
    if name in ("Edit", "MultiEdit", "NotebookEdit") and inp.get("file_path"):
        return "edit " + os.path.basename(str(inp["file_path"]))
    if name == "Write" and inp.get("file_path"):
        return "create " + os.path.basename(str(inp["file_path"]))
    if name in ("WebFetch", "WebSearch") and (inp.get("url") or inp.get("query")):
        return "fetch " + urlsplit(str(inp["url"])).netloc if inp.get("url") else "search the web for " + str(inp["query"])
    return "use " + " ".join(name.replace("__", "_").split("_")).strip()


def set_status(state: SessionState, status: Status, now: float) -> None:
    if state.status != status:
        state.status = status
        state.status_since = now
        if status in (Status.NEEDS_YOU, Status.DONE):
            state.seen = False


def apply_hook(state: SessionState, event: dict, now: float) -> None:
    name = event.get("hook_event_name", "")
    if event.get("transcript_path"):
        state.transcript_path = event["transcript_path"]
    if name == "SessionEnd":
        state.hook_seen = False
        state.message = ""
        set_status(state, Status.IDLE, now)
        return
    state.hook_seen = True
    if name == "SessionStart":
        state.message = ""
        set_status(state, Status.IDLE, now)
    elif name in WORKING_EVENTS:
        state.message = ""
        state.pending_tool = (describe_tool(event.get("tool_name", ""), event.get("tool_input"))
                              if name == "PreToolUse" else "")
        set_status(state, Status.WORKING, now)
    elif name == "Notification":
        if event.get("notification_type") == "idle_prompt":
            return
        if event.get("notification_type") == "permission_prompt" and state.pending_tool:
            state.message = "wants to " + state.pending_tool
        else:
            state.message = event.get("message", "")
        set_status(state, Status.NEEDS_YOU, now)
    elif name == "Stop":
        state.message = (event.get("last_assistant_message") or "").strip().split("\n")[0]
        set_status(state, Status.DONE, now)


def apply_heuristics(state: SessionState, now: float) -> None:
    shell_idle = state.job_name in SHELLS
    if state.hook_seen and shell_idle:
        state.hook_seen = False
        state.message = ""
    if not state.hook_seen:
        set_status(state, Status.IDLE if shell_idle else Status.WORKING, now)


def sort_key(state: SessionState) -> tuple:
    if state.status is Status.NEEDS_YOU:
        rank = 0
    elif state.status is Status.DONE and not state.seen:
        rank = 1
    elif state.status is Status.WORKING:
        rank = 2
    elif state.status is Status.DONE:
        rank = 3
    else:
        rank = 4
    return (rank, -state.status_since)
