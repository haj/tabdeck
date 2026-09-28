import pytest

from tabdeck.tmux_source import TmuxSource


class FakeTmux:
    def __init__(self, panes="", windows="", fail_list=False, deck=True):
        self.panes, self.windows, self.fail_list, self.calls = panes, windows, fail_list, []
        self.deck = deck

    async def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "list-panes":
            if self.fail_list:
                return 1, "no server running on /tmp/tmux-1000/default"
            if args[-1] == "#{pane_id}":
                return 0, "\n".join(l.split("\t")[0] for l in self.panes.splitlines())
            return 0, self.panes
        if args[0] == "capture-pane":
            return 0, f"screen of {args[-1]}\n\n"
        if args[0] == "has-session":
            return (0, "") if self.deck else (1, "can't find session: deck")
        if args[0] == "list-windows":
            return 0, self.windows
        if args[0] == "new-window":
            return 0, "%7\n"
        return 0, ""


PANES = "%3\tAtlas\t/home/dev/Projects/Atlas\tclaude\t/dev/pts/2\t4242\n%5\tscratch\t/home/dev\tbash\t/dev/pts/3\t4300"


def test_ids_round_trip():
    assert TmuxSource.to_sid("%3") == "tmux-3" and TmuxSource.to_pane("tmux-3") == "%3"
    with pytest.raises(KeyError):
        TmuxSource.to_pane("A1B2")


async def test_snapshot_parses_panes():
    snaps = await TmuxSource(run=FakeTmux(PANES)).snapshot()
    assert [(s.session_id, s.tab_title, s.cwd, s.job_name, s.shell_pid) for s in snaps] == [
        ("tmux-3", "Atlas", "/home/dev/Projects/Atlas", "claude", 4242),
        ("tmux-5", "scratch", "/home/dev", "bash", 4300)]
    assert snaps[0].screen_text == "screen of %3"


async def test_no_server_means_no_sessions():
    assert await TmuxSource(run=FakeTmux(fail_list=True)).snapshot() == []


async def test_missing_tmux_raises_so_the_hub_marks_source_down():
    async def missing(*args):
        raise FileNotFoundError("tmux")
    with pytest.raises(FileNotFoundError):
        await TmuxSource(run=missing).snapshot()


async def test_send_text_is_literal_then_enter():
    fake = FakeTmux(PANES)
    await TmuxSource(run=fake).send_text("tmux-3", "fix it; rm -rf x\nnow")
    assert fake.calls[-2] == ("send-keys", "-t", "%3", "-l", "fix it; rm -rf x now")
    assert fake.calls[-1] == ("send-keys", "-t", "%3", "Enter")


async def test_send_keys_maps_enter_escape_and_digits():
    fake = FakeTmux(PANES)
    src = TmuxSource(run=fake)
    await src.send_keys("tmux-3", "\r")
    await src.send_keys("tmux-3", "\x1b")
    await src.send_keys("tmux-3", "2")
    keys = [c for c in fake.calls if c[0] == "send-keys"]
    assert keys == [("send-keys", "-t", "%3", "Enter"), ("send-keys", "-t", "%3", "Escape"),
                    ("send-keys", "-t", "%3", "-l", "2")]


async def test_unknown_pane_is_keyerror():
    with pytest.raises(KeyError):
        await TmuxSource(run=FakeTmux(PANES)).send_text("tmux-9", "x")


async def test_create_tab_opens_a_uniquely_named_deck_window():
    fake = FakeTmux(PANES, windows="_keep\nAtlas\nscratch")
    sid = await TmuxSource(run=fake).create_tab("/home/dev/Projects/Atlas", "claude")
    assert sid == "tmux-7"
    assert ("new-session", "-d", "-s", "deck", "-n", "_keep") not in fake.calls
    assert fake.calls[-1] == ("new-window", "-d", "-t", "=deck:", "-n", "Atlas-2", "-c",
                              "/home/dev/Projects/Atlas", "-P", "-F", "#{pane_id}", "claude")
    fake2 = FakeTmux(PANES, deck=False)
    await TmuxSource(run=fake2).create_tab("/home/dev/Projects/my app!", None)
    assert ("new-session", "-d", "-s", "deck", "-n", "_keep") in fake2.calls  # deck made first
    assert fake2.calls[-1][5] == "my-app-" and fake2.calls[-1][-1] == "#{pane_id}"


async def test_snapshot_lists_deck_windows_with_server_and_pane():
    fake = FakeTmux(PANES)
    snaps = await TmuxSource(run=fake, server="hub1").snapshot()
    assert fake.calls[0][:4] == ("list-panes", "-s", "-t", "=deck")
    assert [(s.server, s.pane) for s in snaps] == [("hub1", "%3"), ("hub1", "%5")]


async def test_close_kills_the_window():
    fake = FakeTmux(PANES)
    await TmuxSource(run=fake).close_session("tmux-3")
    assert fake.calls[-1] == ("kill-window", "-t", "%3")


async def test_down_arrow_is_sent_as_a_key_name():
    fake = FakeTmux(PANES)
    await TmuxSource(run=fake).send_keys("tmux-3", "\x1b[B")
    assert [c for c in fake.calls if c[0] == "send-keys"] == [("send-keys", "-t", "%3", "Down")]


async def test_keepalive_window_is_hidden():
    panes = PANES + "\n%9\t_keep\t/home/dev\tbash\t/dev/pts/9\t1"
    snaps = await TmuxSource(run=FakeTmux(panes)).snapshot()
    assert "tmux-9" not in [s.session_id for s in snaps]
