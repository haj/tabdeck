from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from .projects import valid_name
from .servers import Server

EXCLUDES = ["node_modules", ".gradle", "build", "Pods", "DerivedData", ".DS_Store"]
ACTIVE_SECONDS = 120  # a conversation written to this recently is probably still running


def claude_dir_name(path: str) -> str:
    """Claude Code keeps a project's history in ~/.claude/projects/<path with every other character as "-">."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def newest_session(folder: Path) -> str | None:
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    return files[0].stem if files else None


def rewrite_cwd(text: str, old: str, new: str) -> str:
    """Point the conversation's working-folder fields at the new machine; everything else stays as it was."""
    out = []
    for line in text.splitlines(keepends=True):
        try:
            entry = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        cwd = entry.get("cwd") if isinstance(entry, dict) else None
        if isinstance(cwd, str) and (cwd == old or cwd.startswith(old + "/")):
            entry["cwd"] = new + cwd[len(old):]
            out.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)
    return "".join(out)


def unique_window(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}-{n}", n + 1
    return candidate


def _run(args: list[str], input: str | None = None):
    return subprocess.run(args, input=input, capture_output=True, text=True)


def move_session(project: str, server: Server, *, session: str | None = None, projects_dir: Path, tmux_session: str = "deck",
                 claude_projects: Path, run=_run) -> dict:
    """Copy a Mac project and its Claude conversation to a server and resume it there in a deck window.
    The Mac session is left running: close it once the server's tab works."""
    if not valid_name(project):
        raise SystemExit(f"invalid project name: {project!r}")
    local = projects_dir / project
    if not local.is_dir():
        raise SystemExit(f"no project folder {local}")
    history = claude_projects / claude_dir_name(str(local))
    session = session or newest_session(history)
    if not session or not re.fullmatch(r"[A-Za-z0-9-]+", session) or not (history / f"{session}.jsonl").exists():
        raise SystemExit(f"no Claude conversation {session or ''} in {history}")
    transcript = history / f"{session}.jsonl"
    active = time.time() - transcript.stat().st_mtime < ACTIVE_SECONDS

    def step(args: list[str], what: str, input: str | None = None) -> str:
        r = run(args, input=input)
        if r.returncode != 0:
            raise SystemExit(f"{what} failed: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def remote(cmd: str, what: str, input: str | None = None) -> str:
        return step(["ssh", "-o", "ConnectTimeout=10", server.ssh, cmd], what, input)

    home = remote("echo $HOME", "reaching the server").strip()
    target = f"{home}/{server.projects}/{project}"
    step(["rsync", "-a", *[a for e in EXCLUDES for a in ("--exclude", e)], f"{local}/", f"{server.ssh}:{target}/"],
         "copying the project")
    dest = f".claude/projects/{claude_dir_name(target)}"
    remote(f"mkdir -p {shlex.quote(dest)}", "making the history folder")
    remote(f"cat > {shlex.quote(f'{dest}/{session}.jsonl')} && chmod 600 {shlex.quote(f'{dest}/{session}.jsonl')}",
           "copying the conversation", input=rewrite_cwd(transcript.read_text(), str(local), target))
    extras = [str(history / n) for n in (session, "memory") if (history / n).is_dir()]
    if extras:
        step(["rsync", "-a", *extras, f"{server.ssh}:{dest}/"], "copying the conversation's files")
    tmux = "PATH=$HOME/.local/bin:$PATH tmux"
    taken = set(remote(f"{tmux} list-windows -t ={tmux_session} -F '#{{window_name}}' 2>/dev/null || true",
                       "listing session windows").split())
    window = unique_window(project, taken)
    remote(f"{tmux} has-session -t ={tmux_session} 2>/dev/null || {tmux} new-session -d -s {tmux_session} -n _keep; "
           f"{tmux} new-window -d -t ={tmux_session}: -n {shlex.quote(window)} -c {shlex.quote(target)} "
           f"{shlex.quote(f'claude --resume {session}')}", "starting Claude")
    return {"server": server.name, "window": window, "session": session, "path": target, "active": active}
