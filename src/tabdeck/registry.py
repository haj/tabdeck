from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .speech import chunk, clean_for_speech, display_names
from .status import (
    SessionState, Status, apply_heuristics, apply_hook, set_status, sort_key,
)
from .transcript import last_assistant_text
from .urls import extract_urls

MAX_PENDING = 50
MAX_SCREEN_URLS = 10
MAX_SUMMARY = 250
MAX_ACTIVITY = 200


@dataclass(frozen=True)
class Snapshot:
    session_id: str
    tab_title: str
    cwd: str
    job_name: str
    tty: str
    shell_pid: int
    screen_text: str
    offline: bool = False  # a remote agent's tab while that agent is disconnected
    urls: tuple | None = None  # set by remote agents, which detect their own app URLs
    url_host: str | None = None  # where those URLs are reachable (the agent's NetBird IP)
    remote: bool = False  # belongs to a remote agent (the Mac), not this machine
    server: str = ""  # the server a tmux session runs on (its name in servers.json); "" for a Mac tab
    pane: str = ""  # its tmux pane id, e.g. "%3"


class Registry:
    def __init__(self, prefs_file: Path | None = None):
        self.sessions: dict[str, SessionState] = {}
        self.version = 0
        self.iterm_ok = False
        self.active: str | None = None  # the tab currently selected in iTerm
        self._pending: dict[str, list[dict]] = {}
        self._hook_seq: dict[str, int] = {}
        self._activity: list[dict] = []
        self._prefs_file = prefs_file
        self.prefs = self._load_prefs()

    # ---- prefs ----
    def _load_prefs(self) -> dict:
        if self._prefs_file and self._prefs_file.exists():
            try:
                data = json.loads(self._prefs_file.read_text())
                return {"pins": data.get("pins", {}), "hidden": data.get("hidden", {})}
            except ValueError:
                pass
        return {"pins": {}, "hidden": {}}

    def _save_prefs(self) -> None:
        if self._prefs_file:
            self._prefs_file.parent.mkdir(parents=True, exist_ok=True)
            self._prefs_file.write_text(json.dumps(self.prefs, indent=2))

    def prefs_for(self, cwd: str) -> tuple[list[str], list[str]]:
        return list(self.prefs["pins"].get(cwd, [])), list(self.prefs["hidden"].get(cwd, []))

    def pin_url(self, sid: str, url: str) -> None:
        s = self.sessions[sid]
        pins = self.prefs["pins"].setdefault(s.cwd, [])
        if url not in pins:
            pins.insert(0, url)
        hidden = self.prefs["hidden"].get(s.cwd, [])
        if url in hidden:
            hidden.remove(url)
        self._save_prefs()
        self.set_urls(sid, [url] + [u for u in s.urls if u != url])

    def hide_url(self, sid: str, url: str) -> None:
        s = self.sessions[sid]
        hidden = self.prefs["hidden"].setdefault(s.cwd, [])
        if url not in hidden:
            hidden.append(url)
        pins = self.prefs["pins"].get(s.cwd, [])
        if url in pins:
            pins.remove(url)
        self._save_prefs()
        self.set_urls(sid, [u for u in s.urls if u != url])

    def reset_urls(self, sid: str) -> None:
        cwd = self.sessions[sid].cwd
        self.prefs["pins"].pop(cwd, None)
        self.prefs["hidden"].pop(cwd, None)
        self._save_prefs()

    # ---- state ----
    def _bump(self) -> None:
        self.version += 1

    def ordered(self) -> list[SessionState]:
        return sorted(self.sessions.values(), key=sort_key)

    def names(self) -> dict[str, str]:
        return display_names(list(self.sessions.values()))

    def set_active(self, sid: str | None) -> None:
        sid = sid if sid in self.sessions else None
        if sid != self.active:
            self.active = sid
            self._bump()

    def set_iterm_ok(self, ok: bool) -> None:
        if ok != self.iterm_ok:
            self.iterm_ok = ok
            self._bump()

    def update(self, snaps: list[Snapshot], now: float) -> None:
        changed = False
        ids = {s.session_id for s in snaps}
        for sid in list(self.sessions):
            if sid not in ids:
                del self.sessions[sid]
                changed = True
        for snap in snaps:
            s = self.sessions.get(snap.session_id)
            if s is None:
                s = SessionState(session_id=snap.session_id, status_since=now)
                self.sessions[snap.session_id] = s
                changed = True
            before = (s.tab_title, s.cwd, s.job_name, s.status, s.message, s.seen, s.offline, tuple(s.urls), s.server, s.pane)
            s.tab_title, s.cwd, s.job_name = snap.tab_title, snap.cwd, snap.job_name
            s.offline, s.url_host, s.remote = snap.offline, snap.url_host, snap.remote
            s.server, s.pane = snap.server, snap.pane
            if snap.urls is not None:
                s.urls = list(snap.urls)
            s.tty, s.shell_pid = snap.tty, snap.shell_pid
            if snap.screen_text != s.screen_text:
                s.screen_text = snap.screen_text
                s.screen_changed_at = now
                for u in extract_urls(snap.screen_text):
                    if u not in s.screen_urls:
                        s.screen_urls = (s.screen_urls + [u])[-MAX_SCREEN_URLS:]
            for event in self._pending.pop(snap.session_id, []):
                self._apply_hook(s, event, now)
            apply_heuristics(s, now)
            if before != (s.tab_title, s.cwd, s.job_name, s.status, s.message, s.seen, s.offline, tuple(s.urls), s.server, s.pane):
                changed = True
        if changed:
            self._bump()

    def hook_seq(self, iterm_session: str) -> int:
        return self._hook_seq.get(iterm_session.split(":")[-1], 0)

    def apply_hook(self, iterm_session: str, event: dict, now: float, if_seq: int | None = None) -> None:
        """Apply a Claude Code hook event. With if_seq, skip it when another event arrived meanwhile."""
        sid = iterm_session.split(":")[-1]
        if if_seq is not None and self._hook_seq.get(sid, 0) != if_seq:
            return
        self._hook_seq[sid] = self._hook_seq.get(sid, 0) + 1
        s = self.sessions.get(sid)
        if s is None:
            if sid in self._pending or len(self._pending) < MAX_PENDING:
                self._pending.setdefault(sid, []).append(event)
                del self._pending[sid][:-20]
            return
        self._apply_hook(s, event, now)
        self._bump()

    def record(self, kind: str, sid: str, text: str, now: float) -> None:
        """Remember something for "catch me up": done, needs_you, sent, approved, denied."""
        name = self.names().get(sid, "a tab")
        self._activity = (self._activity + [{"time": now, "kind": kind, "tab": name, "text": text}])[-MAX_ACTIVITY:]

    def activity(self, since: float) -> list[dict]:
        return [e for e in self._activity if e["time"] > since]

    def _apply_hook(self, s: SessionState, event: dict, now: float) -> None:
        before = s.status
        self._apply_hook_state(s, event, now)
        if s.status != before and s.status in (Status.DONE, Status.NEEDS_YOU):
            self.record("done" if s.status is Status.DONE else "needs_you", s.session_id, s.message, now)

    def _apply_hook_state(self, s: SessionState, event: dict, now: float) -> None:
        apply_hook(s, event, now)
        if event.get("hook_event_name") == "Stop" and event.get("summary"):
            s.message = str(event["summary"])[:2 * MAX_SUMMARY]
        elif event.get("hook_event_name") == "Stop" and s.transcript_path:
            text = last_assistant_text(s.transcript_path)
            if text:
                # A short summary Jarvis can speak and reason about: the first two sentences.
                first = chunk(clean_for_speech(text), 2)
                s.message = first[0][:MAX_SUMMARY] if first else ""

    def mark(self, sid: str, status: Status, message: str, now: float, seen: bool = True) -> None:
        s = self.sessions[sid]
        set_status(s, status, now)
        s.message = message
        s.seen = seen
        self._bump()

    def reset_hooks(self, sid: str) -> None:
        s = self.sessions.get(sid)
        if s is not None:
            s.hook_seen, s.message, s.pending_tool = False, "", ""
        self._pending.pop(sid, None)

    def mark_seen(self, sid: str) -> None:
        s = self.sessions[sid]
        if not s.seen:
            s.seen = True
            self._bump()

    def set_urls(self, sid: str, urls: list[str]) -> None:
        s = self.sessions.get(sid)
        if s is not None and s.urls != urls:
            s.urls = list(urls)
            self._bump()
