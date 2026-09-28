from __future__ import annotations

import json
import os

TAIL_BYTES = 2_000_000


def _tail_lines(path: str) -> list[str]:
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        start = max(0, size - TAIL_BYTES)
        f.seek(start)
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if start > 0 else lines


def last_assistant_text(path: str) -> str:
    try:
        lines = _tail_lines(path)
    except OSError:
        return ""
    parts: list[str] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if kind == "assistant":
            content = entry.get("message", {}).get("content", [])
            if isinstance(content, str):
                texts = [content]
            else:
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
            for t in reversed(texts):
                if t.strip():
                    parts.append(t.strip())
        elif kind == "user" and (parts or _is_prompt(entry)):
            # Never go back past the user's latest prompt: if the reply to it is not written
            # yet, return nothing rather than the previous turn's answer.
            break
    return "\n\n".join(reversed(parts)).strip()


def _is_prompt(entry: dict) -> bool:
    """A real user message, as opposed to a tool result fed back to Claude."""
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "text" for b in content)
