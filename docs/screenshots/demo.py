"""A hub with made-up sessions, for README screenshots: no real servers, projects or paths.

Run: uv run python docs/screenshots/demo.py  (serves http://127.0.0.1:8799), then docs/screenshots/shoot.py.
With DEMO_CERT and DEMO_KEY set it serves https, for the widget (docs/screenshots/widget.sh).
"""
import os
import tempfile
import time
from pathlib import Path

import uvicorn

from tabdeck.auth import Auth
from tabdeck.config import Config
from tabdeck.registry import Registry, Snapshot
from tabdeck.status import Status
from tabdeck.web import create_app

API_SCREEN = """\
✻ Welcome back!

> add rate limiting to the login endpoint

⏺ I'll add a sliding-window limiter in front of POST /auth/login.

⏺ Update(src/auth/limits.py)
  ⎿  Added 38 lines

⏺ Bash(pytest tests/auth -q)
  ⎿  42 passed in 3.1s

Allow Claude to run `git commit -am "Rate-limit login"`?
❯ 1. Yes
  2. Yes, and don't ask again for git commit
  3. No, and tell Claude what to do differently
"""

WEB_SCREEN = """\
> fix the flaky checkout test

⏺ Reading tests/e2e/checkout.spec.ts …
⏺ The test clicks "Pay" before the Stripe iframe is ready.
⏺ Update(tests/e2e/checkout.spec.ts)
  ⎿  Wait for the payment frame before clicking
⏺ Bash(npx playwright test checkout --repeat-each 20)
  ⎿  Running 20 tests…
"""

DOCS_SCREEN = """\
> write the upgrade guide for v2

⏺ Write(docs/upgrading.md)
  ⎿  Wrote 112 lines

⏺ The guide covers the config rename, the new CLI flags and the removed
  endpoints, with a checklist at the end. Preview: http://localhost:4173/upgrading
"""

SESSIONS = [
    # id, project path, server, screen, status, message, seen
    ("tmux-1", "/home/dev/Projects/api", "hub", API_SCREEN, Status.NEEDS_YOU,
     'Wants to run: git commit -am "Rate-limit login"', False),
    ("tmux-2", "/home/dev/Projects/web", "hub", WEB_SCREEN, Status.WORKING,
     "run: npx playwright test checkout --repeat-each 20", True),
    ("tmux-3", "/home/dev/Projects/docs", "gpu", DOCS_SCREEN, Status.DONE,
     "Wrote the v2 upgrade guide: config rename, new CLI flags, removed endpoints, and a checklist.", False),
    ("tmux-4", "/home/dev/Projects/infra", "gpu", "> \n", Status.IDLE, "", True),
]


def snapshots():
    return [Snapshot(sid, "claude", cwd, "claude", "", 0, screen, server=server, pane=f"%{i}")
            for i, (sid, cwd, server, screen, *_rest) in enumerate(SESSIONS, 1)]


class DemoBridge:
    async def send_text(self, sid, text): pass
    async def send_keys(self, sid, keys): pass
    async def create_tab(self, cwd, command): return "tmux-9"
    async def snapshot(self): return snapshots()
    async def active_session(self): return None
    async def focus(self, sid): pass


def main():
    root = Path(tempfile.mkdtemp(prefix="tabdeck-demo-"))
    for name in ("api", "web", "docs", "infra", "mobile"):
        (root / "Projects" / name).mkdir(parents=True)
    config = Config(data_dir=root, projects_dir=root / "Projects", hub_server="hub", intent_model="llama3.2")
    registry = Registry()
    registry.set_iterm_ok(True)
    now = time.time()
    registry.update(snapshots(), now)
    for (sid, _cwd, _server, _screen, status, message, seen), ago in zip(SESSIONS, (40, 300, 720, 3600)):
        registry.sessions[sid].hook_seen = True  # keep these statuses: no screen heuristics
        registry.mark(sid, status, message, now - ago, seen=seen)
    registry.set_urls("tmux-3", ["http://localhost:4173/upgrading"])
    app = create_app(registry=registry, bridge=DemoBridge(), auth=Auth(config.tokens_file),
                     transcriber=None, config=config, tts_voices=lambda url: _voices())
    # DEMO_CERT/DEMO_KEY: serve HTTPS (the widget connects over https/wss), e.g. with this Mac's mkcert pair.
    tls = {"ssl_certfile": os.environ["DEMO_CERT"], "ssl_keyfile": os.environ["DEMO_KEY"]} if os.environ.get("DEMO_CERT") else {}
    uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning", **tls)


async def _voices():
    return []


if __name__ == "__main__":
    main()
