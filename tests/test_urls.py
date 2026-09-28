from tabdeck.urls import (
    Listener, descendants, extract_urls, needs_forward, parse_lsof, parse_ps,
    phone_href, session_urls, url_port,
)


def test_extract_urls_normalizes_hosts_and_paths():
    text = ("  VITE ready\n  ➜  Local:   http://localhost:5173/\n  ➜  Network: http://192.0.2.80:5173/\n"
            "Server on http://127.0.0.1:8000/docs.\nhttps://0.0.0.0:8443/app, http://[::1]:3000")
    assert extract_urls(text) == [
        "http://localhost:5173/", "http://localhost:8000/docs",
        "https://localhost:8443/app", "http://localhost:3000/",
    ]


def test_extract_ignores_non_local_hostnames():
    assert extract_urls("see https://github.com/x and http://example.com:8080/") == []


def test_url_port():
    assert url_port("http://localhost:5173/") == 5173
    assert url_port("nonsense") is None


def test_process_tree():
    ps = "  1     0\n 100     1\n 200   100\n 201   100\n 300   200\n 999     1\n"
    children = parse_ps(ps)
    assert descendants(children, 100) == {100, 200, 201, 300}


def test_parse_lsof():
    out = "p123\nf10\nn*:3000\np456\nf5\nn127.0.0.1:5173\nf6\nn[::1]:5173\n"
    assert parse_lsof(out) == [Listener(123, "*", 3000), Listener(456, "127.0.0.1", 5173),
                               Listener(456, "[::1]", 5173)]


def test_session_urls_prefers_screen_paths_then_own_ports():
    listeners = [Listener(300, "127.0.0.1", 5173), Listener(301, "*", 8000), Listener(9, "*", 9999)]
    urls = session_urls(["http://localhost:5173/app", "http://localhost:4000/"], listeners, {300, 301})
    assert urls == ["http://localhost:5173/app", "http://localhost:8000/"]


def test_session_urls_pins_and_hidden():
    listeners = [Listener(300, "127.0.0.1", 5173)]
    urls = session_urls([], listeners, {300}, pins=["http://localhost:3000/admin"],
                        hidden=["http://localhost:5173/"])
    assert urls == ["http://localhost:3000/admin"]


def test_phone_href():
    assert phone_href("http://localhost:5173/app?x=1", "192.0.2.20") == "http://192.0.2.20:5173/app?x=1"


def test_needs_forward():
    ls = [Listener(1, "127.0.0.1", 5173), Listener(2, "*", 8000)]
    assert needs_forward(5173, ls, "192.0.2.20") is True
    assert needs_forward(8000, ls, "192.0.2.20") is False
