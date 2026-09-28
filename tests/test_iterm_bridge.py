import pytest

pytest.importorskip("iterm2")  # macOS only; also needs a running iTerm2 (marked "iterm")

import asyncio
import os

import pytest

from tabdeck.iterm_bridge import ItermBridge

pytestmark = pytest.mark.iterm


async def wait_for(bridge, sid, predicate, tries=50):
    for _ in range(tries):
        for snap in await bridge.snapshot():
            if snap.session_id == sid and predicate(snap):
                return snap
        await asyncio.sleep(0.2)
    raise AssertionError("condition not met")


async def test_roundtrip(tmp_path):
    bridge = ItermBridge()
    await bridge.connect()
    assert bridge.connected
    sid = await bridge.create_tab(str(tmp_path), "echo tabdeck-probe-1")
    try:
        snap = await wait_for(bridge, sid, lambda s: "tabdeck-probe-1" in s.screen_text
                              and os.path.realpath(s.cwd) == os.path.realpath(tmp_path))
        assert snap.job_name in {"zsh", "-zsh", "bash"}
        assert snap.tty.startswith("/dev/ttys") and snap.shell_pid > 0
        await bridge.send_text(sid, "echo hello\nfrom-tabdeck")
        await wait_for(bridge, sid, lambda s: "hello from-tabdeck" in s.screen_text)
        await bridge.focus(sid)
        for _ in range(20):  # iTerm reports the focus change asynchronously
            if await bridge.active_session() == sid:
                break
            await asyncio.sleep(0.1)
        assert await bridge.active_session() == sid
    finally:
        await bridge.close_session(sid)
    with pytest.raises(KeyError):
        await bridge.send_keys("no-such-session", "x")
