from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .speech import describe, summary
from .status import SessionState, Status

PHRASES: dict[str, set[str]] = {
    "status": {"status", "whats going on", "what is going on", "summary", "update"},
    "catch_up": {"catch me up", "catch up", "what did i miss", "what happened", "what happened while i was away"},
    "next": {"next", "next one", "next tab"},
    "approve": {"approve", "approved", "yes", "yes please", "yep", "allow"},
    "deny": {"deny", "denied", "no", "reject", "nope"},
    "always": {"always allow", "always", "yes always", "allow always", "dont ask again", "always approve"},
    "read": {"read it", "read", "read that", "what did it say", "read the reply"},
    "read_more": {"read more", "more", "continue reading", "keep reading"},
    "stop": {"stop", "interrupt", "escape"},
    "open": {"open the app", "open app", "open it", "open the url", "open url", "show me the app"},
    "cancel": {"cancel", "never mind", "nevermind", "scratch that"},
    "send": {"send", "send it", "send now"},
}
GOTO = re.compile(r"^(?:go to|switch to|select|open tab)\s+(.+)$")
TAB_N = re.compile(r"^tab\s+(\w+)$")
NEW = re.compile(r"^(?:new session|new claude session|start claude|new opencode session|start opencode|start open code|start a session|start a new session|new claude)"
                 r"\s+(?:in|for|on)\s+(.+?)(?:\s+and\s+(?:then\s+)?(?:tell|ask|have)\s+it\b.*)?$")
ON_MAC = re.compile(r"\s*\bon (?:my|the) mac\b")
ON_MAC_RAW = re.compile(r"\s*\bon (?:my|the) mac\b", re.I)
NEW_TASK = re.compile(r"\s+and\s+(?:then\s+)?(?:tell|ask|have)\s+it\s+(?:to\s+)?(.+)$", re.I | re.S)
NUMBERS = {"one": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4, "for": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# The wake word must open the utterance or a sentence (Whisper sometimes prefixes noise words).
# Wake words, as Whisper writes them. Any other word (or two) works too: "hey"/"okay" is optional before it.
WAKE_PRESETS = {
    "jarvis": r"(?:(?:hey|okay|ok)[\s,]*)?jarvis\b",
}


def wake_pattern(word: str) -> re.Pattern:
    """The wake word must open the utterance or a sentence (Whisper sometimes prefixes noise words)."""
    core = WAKE_PRESETS.get(" ".join(word.lower().split()))
    if core is None:
        core = r"(?:(?:hey|okay|ok)[\s,]*)?" + r"[\s,]*".join(map(re.escape, word.split())) + r"\b"
    return re.compile(r"(?:^|[.!?]\s+)\s*" + core + r"[\s,.!?:;-]*", re.I)


WAKE = wake_pattern("jarvis")
TELL = re.compile(r"^\s*(?:tell|ask)\s+(.+)$", re.I | re.S)
TELL_SPLIT = re.compile(r"\s+to\s+|\s*[,:]\s*", re.I)
SELF_NAMES = {"it", "claude", "opencode", "open code", "them", "him", "her", "this tab", "this one"}


def strip_wake(text: str, pattern: re.Pattern | None = None) -> str | None:
    m = (pattern or WAKE).search(text)
    return text[m.end():].strip() if m else None


@dataclass(frozen=True)
class Command:
    kind: str
    arg: str = ""
    text: str = ""
    task: str = ""  # new_session: first message for the new Claude session
    where: str = ""  # new_session: "mac" (an iTerm tab) or a server name; "" for the hub


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("'", "")
    t = re.sub(r"[^\w\s]", " ", t).replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def _digits(text: str) -> str:
    return " ".join(str(NUMBERS[w]) if w in NUMBERS else w for w in text.split())


def parse(text: str) -> Command:
    norm = normalize(text)
    if not norm:
        return Command("empty", text=text)
    for kind, phrases in PHRASES.items():
        if norm in phrases:
            return Command(kind, text=text)
    if (m := TELL.match(text)) and TELL_SPLIT.search(m.group(1)):
        return Command("tell", m.group(1).strip(), text)
    if m := TAB_N.match(norm):
        return Command("tab", m.group(1), text)
    on_mac = ON_MAC.search(norm)
    if m := NEW.match(ON_MAC.sub("", norm).strip()):
        task = NEW_TASK.search(ON_MAC_RAW.sub("", text))
        return Command("new_session", m.group(1), text, task.group(1).strip() if task else "",
                       "mac" if on_mac else "")
    if m := GOTO.match(norm):
        return Command("goto", m.group(1), text)
    return Command("reply", text=text.strip())


def match_name(query: str, options: dict[str, str]) -> str | None:
    q = _digits(normalize(query))
    norm = {k: _digits(normalize(v)) for k, v in options.items()}
    exact = [k for k, v in norm.items() if v == q]
    if len(exact) == 1:
        return exact[0]
    contains = [k for k, v in norm.items() if q and q in v]
    if len(contains) == 1:
        return contains[0]
    close = difflib.get_close_matches(q, list(norm.values()), n=1, cutoff=0.6)
    if close:
        hits = [k for k, v in norm.items() if v == close[0]]
        if len(hits) == 1:
            return hits[0]
    return None


def _ask(text: str) -> dict:
    return {"type": "ask", "text": text}


def _select(s: SessionState, names: dict[str, str]) -> dict:
    return {"type": "select", "session_id": s.session_id, "speak": describe(s, names[s.session_id])}


def resolve(cmd: Command, sessions: list[SessionState], names: dict[str, str],
            selected: str | None, projects: list[str], agent_name: str = "Claude") -> dict:
    by_id = {s.session_id: s for s in sessions}
    sel = by_id.get(selected) if selected else None
    k = cmd.kind
    if k == "empty":
        return _ask("I didn't catch that.")
    if k == "status":
        return {"type": "speak", "text": summary(sessions, names)}
    if k == "cancel":
        return {"type": "cancel"}
    if k == "catch_up":
        return {"type": "catch_up"}
    if k == "send":
        return {"type": "send"}
    if k == "next":
        cands = [s for s in sessions
                 if s.status is Status.NEEDS_YOU or (s.status is Status.DONE and not s.seen)]
        others = [s for s in cands if s.session_id != selected]
        pick = (others or cands or [None])[0]
        if pick is None:
            return {"type": "speak", "text": "Nothing needs you right now."}
        return _select(pick, names)
    if k == "tab":
        n = int(cmd.arg) if cmd.arg.isdigit() else NUMBERS.get(cmd.arg)
        if not n or n > len(sessions):
            return _ask(f"There is no tab {cmd.arg}.")
        return _select(sessions[n - 1], names)
    if k == "goto":
        sid = match_name(cmd.arg, {s.session_id: names[s.session_id] for s in sessions})
        if sid is None:
            return _ask(f"I couldn't find a tab called {cmd.arg}.")
        return _select(by_id[sid], names)
    if k == "tell":
        options = {s.session_id: names[s.session_id] for s in sessions}
        splits = list(TELL_SPLIT.finditer(cmd.arg))
        for m in reversed(splits):  # longest candidate name first
            name, message = cmd.arg[:m.start()].strip(), cmd.arg[m.end():].strip()
            if not message:
                continue
            if normalize(name) in SELF_NAMES:
                sid = sel.session_id if sel else None
            else:
                sid = match_name(name, options)
            if sid:
                return {"type": "reply", "session_id": sid, "text": message,
                        "speak": f"Sending to {names[sid]}."}
        return _ask("I couldn't find that tab. Say tell, a tab name, to, then your message.")
    if k == "new_session":
        project = match_name(cmd.arg, {p: p for p in projects})
        if project is None:
            return _ask(f"I couldn't find a project called {cmd.arg}.")
        where = {"where": cmd.where} if cmd.where else {}
        place = " on your Mac" if cmd.where == "mac" else (f" on {cmd.where}" if cmd.where else "")
        if cmd.task:
            return {"type": "new_session", "project": project, "task": cmd.task, **where,
                    "speak": f"Starting {agent_name} in {project}{place}, then I'll send your task."}
        return {"type": "new_session", "project": project, **where, "speak": f"Starting {agent_name} in {project}{place}."}

    if sel is None:
        return _ask("Which tab? Say go to, then a name.")
    name = names[sel.session_id]
    sid = sel.session_id
    if k in ("approve", "deny", "always"):
        if sel.status is not Status.NEEDS_YOU:
            return _ask(f"{name} isn't asking for anything.")
        if k == "approve":
            return {"type": "keys", "session_id": sid, "key": "enter", "speak": "Approved."}
        if k == "always":
            return {"type": "keys", "session_id": sid, "key": "2", "speak": "Always allowed."}
        return {"type": "keys", "session_id": sid, "key": "escape", "speak": "Denied."}
    if k == "stop":
        return {"type": "keys", "session_id": sid, "key": "escape", "speak": f"Stopping {name}."}
    if k == "read":
        return {"type": "read", "session_id": sid}
    if k == "read_more":
        return {"type": "read_more", "session_id": sid}
    if k == "open":
        if not sel.urls:
            return _ask(f"No app URL found for {name}.")
        return {"type": "open_url", "session_id": sid, "index": 0}
    return {"type": "reply", "session_id": sid, "text": cmd.text, "speak": f"Sending to {name}."}
