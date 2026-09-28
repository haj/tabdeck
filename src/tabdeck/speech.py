from __future__ import annotations

import re

from .status import SessionState, Status

CODE_BLOCK = re.compile(r"```.*?```", re.S)
TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.M)
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL = re.compile(r"https?://\S+")
INLINE_CODE = re.compile(r"`([^`]*)`")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
MD_SYMBOLS = re.compile(r"[*#>~]+")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def clean_for_speech(text: str) -> str:
    text = CODE_BLOCK.sub(" (code omitted). ", text)
    text = TABLE_LINE.sub("", text)
    text = LINK.sub(r"\1", text)
    text = URL.sub("a link", text)
    text = INLINE_CODE.sub(r"\1", text)
    text = text.replace("_", " ")
    lines = []
    for line in text.splitlines():
        line = MD_SYMBOLS.sub("", BULLET.sub("", line)).strip()
        if not line:
            continue
        if line[-1] not in ".!?:":
            line += "."
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def chunk(text: str, sentences: int = 3) -> list[str]:
    parts = [p for p in SENTENCE.split(text.strip()) if p]
    return [" ".join(parts[i:i + sentences]) for i in range(0, len(parts), sentences)]


def display_names(sessions: list[SessionState]) -> dict[str, str]:
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for s in sessions:
        base = s.project
        counts[base] = counts.get(base, 0) + 1
        names[s.session_id] = base if counts[base] == 1 else f"{base} {counts[base]}"
    return names


def describe(state: SessionState, name: str) -> str:
    if state.status is Status.NEEDS_YOU:
        if state.message.startswith("wants to "):
            return f"{name} {state.message}. Approve?"
        return f"{name} needs you. {state.message}".strip()
    if state.status is Status.DONE:
        return f"{name} is done. {state.message}".strip()
    if state.status is Status.WORKING:
        return f"{name} is working."
    return f"{name} is idle."


def summary(sessions: list[SessionState], names: dict[str, str]) -> str:
    need = [s for s in sessions if s.status is Status.NEEDS_YOU]
    done = [s for s in sessions if s.status is Status.DONE and not s.seen]
    working = [s for s in sessions if s.status is Status.WORKING]
    if not (need or done or working):
        return "All quiet. Nothing needs you."
    parts = []
    if need:
        head = f"{len(need)} {'needs' if len(need) == 1 else 'need'} you."
        parts.append(head + " " + " ".join(describe(s, names[s.session_id]) for s in need))
    if done:
        parts.append(" ".join(describe(s, names[s.session_id]) for s in done))
    if working:
        parts.append("Working: " + ", ".join(names[s.session_id] for s in working) + ".")
    return " ".join(parts)
