"""`tabdeck pair`: a one-time pairing link for a phone or browser, from this Mac (no ssh to the hub needed).
The hub hands out codes to its Mac agent's token; the link works once, for 10 minutes."""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Config


def _post(url: str, token: str, cafile: str | None) -> dict:
    r = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, verify=cafile or True, timeout=15)
    r.raise_for_status()
    return r.json()


def request_pairing(config: Config, post=_post) -> str:
    s = config.settings
    hub, token = s.get("hub_url"), s.get("agent_token")
    if not hub or not token:
        raise SystemExit("This Mac isn't connected to a hub yet: run `tabdeck setup` (or `tabdeck install-agent`).")
    url = str(post(hub.rstrip("/") + "/api/pair", token, s.get("hub_ca")).get("url", ""))
    # Only an https link on the hub itself: never a file://, an app link or an option for `open`.
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname != urlsplit(hub).hostname or u.path != "/pair":
        raise SystemExit(f"The hub returned an unexpected pairing link: {url!r}")
    return url


def pair(config: Config, open_here: bool = False) -> None:
    url = request_pairing(config)
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001 - the link alone is enough
        pass
    print(f"\nScan with the phone's camera (on your private network), or open:\n  {url}\n"
          "The link works once, for 10 minutes.")
    if open_here:
        subprocess.run(["open", "--", url], check=False)  # pairs this Mac's browser
        print("Opened in this Mac's browser: it is now paired.")


def _post_local(url: str, body: dict, verify) -> dict:
    r = httpx.post(url, json=body, verify=verify, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise SystemExit(f"The hub said: {detail}")
    return r.json()


def open_folder(config: Config, path: str, task: str = "", post=_post_local) -> str:
    """`tabdeck open PATH` on the hub server: a session in that folder, like opening it in an editor."""
    folder = str(Path(path).expanduser().resolve())
    ca = config.data_dir / "ca.pem"  # the hub's certificate authority (deployed with it); localhost otherwise
    body = {"project": "", "path": folder}
    if task:
        body["task"] = task
    return str(post(f"https://127.0.0.1:{config.port}/api/new_session", body, str(ca) if ca.exists() else False)["id"])
