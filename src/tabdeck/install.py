from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config
from .files import write_private
from .profiles import instance_suffix

SUFFIX = instance_suffix()  # "" or e.g. "-gpu": several instances can live side by side
LABEL = f"com.tabdeck{SUFFIX}.service"
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "Notification", "Stop", "SessionEnd"]
REPO = Path(__file__).resolve().parents[2]
SETTINGS = Path.home() / ".claude" / "settings.json"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + str(Path.home() / ".local/bin")


def merge_hooks(settings: dict, command: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == command for g in groups for h in g.get("hooks", [])):
            continue
        groups.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
    return settings


def remove_hooks(settings: dict, command: str) -> dict:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if h.get("command") != command]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def build_plist(uv: str, repo: Path, log_file: Path, command: str = "serve") -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--project", str(repo), "tabdeck", command],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
        "EnvironmentVariables": {"PATH": PATH, **({"TABDECK_INSTANCE": SUFFIX[1:]} if SUFFIX else {})},
    }


def _bootout(label: str) -> None:
    """Unload a LaunchAgent and wait until launchd has really dropped it; bootout returns early,
    and an immediate bootstrap then fails with error 5."""
    target = f"gui/{os.getuid()}/{label}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True)
    for _ in range(50):
        if subprocess.run(["launchctl", "print", target], capture_output=True).returncode != 0:
            return
        time.sleep(0.1)


def _certs(config: Config) -> None:
    if config.cert_file.exists() and config.key_file.exists():
        print("certificate exists:", config.cert_file)
        return
    mkcert = shutil.which("mkcert") or "/opt/homebrew/bin/mkcert"
    if not Path(mkcert).exists():
        subprocess.run(["brew", "install", "mkcert"], check=True)
    subprocess.run([mkcert, "-install"], check=True)
    hosts = ["localhost", "127.0.0.1"]
    fqdn = os.environ.get("TABDECK_FQDN")  # e.g. this Mac's NetBird name
    if fqdn:
        hosts.append(fqdn)
    if config.netbird_ip:
        hosts.append(config.netbird_ip)
    subprocess.run([mkcert, "-cert-file", str(config.cert_file), "-key-file", str(config.key_file),
                    *hosts], check=True)
    caroot = subprocess.run([mkcert, "-CAROOT"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"\nInstall this on your iPhone (AirDrop it, then Settings → General → VPN & Device "
          f"Management, then Settings → General → About → Certificate Trust Settings):\n  {caroot}/rootCA.pem\n")


def _hook(config: Config) -> None:
    shutil.copy(REPO / "hooks" / "tabdeck-hook.sh", config.hook_file)
    os.chmod(config.hook_file, 0o755)
    (config.data_dir / "instance.env").write_text(f"TABDECK_PORT={config.port}\n")  # the hook posts to this port
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        shutil.copy(SETTINGS, SETTINGS.with_suffix(".json.tabdeck-backup"))
    SETTINGS.write_text(json.dumps(merge_hooks(settings, str(config.hook_file)), indent=2) + "\n")
    print("Claude Code hook installed:", config.hook_file)


def _launchd(config: Config) -> None:
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _bootout(LABEL)
    PLIST.write_bytes(plistlib.dumps(build_plist(uv, REPO, config.log_file)))
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print(f"service started; open https://localhost:{config.port} (logs: {config.log_file})")


def install(config: Config) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _certs(config)
    if config.agent_profile.status == "claude-hooks":
        _hook(config)  # Claude Code reports status through hooks; OpenCode through its plugin
    _launchd(config)


def uninstall(config: Config) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    if SETTINGS.exists():
        settings = json.loads(SETTINGS.read_text())
        SETTINGS.write_text(json.dumps(remove_hooks(settings, str(config.hook_file)), indent=2) + "\n")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{WIDGET_LABEL}"], capture_output=True)
    WIDGET_PLIST.unlink(missing_ok=True)
    print("TabDeck service and hook removed (data kept in", config.data_dir, ")")


WIDGET_LABEL = f"com.tabdeck{SUFFIX}.widget"
WIDGET_APP = Path.home() / "Applications" / f"DeckWidget{SUFFIX}.app"
WIDGET_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{WIDGET_LABEL}.plist"


def build_widget_plist(app: Path, log_file: Path) -> dict:
    return {
        "Label": WIDGET_LABEL,
        "ProgramArguments": [str(app / "Contents" / "MacOS" / "DeckWidget")],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, stay quit after "Quit Deck"
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }


def install_widget(config: Config) -> None:
    subprocess.run(["sh", str(REPO / "widget" / "bundle.sh"), str(WIDGET_APP.parent)], check=True)
    domain = f"gui/{os.getuid()}"
    _bootout(WIDGET_LABEL)
    WIDGET_PLIST.write_bytes(plistlib.dumps(build_widget_plist(WIDGET_APP, config.data_dir / "widget.log")))
    subprocess.run(["launchctl", "bootstrap", domain, str(WIDGET_PLIST)], check=True)
    print(f"{config.assistant_name} widget installed and started. Allow microphone access when macOS asks.")


def deploy_env(path: Path = REPO / "deploy.env") -> dict[str, str]:
    """Machine addresses (HUB_HOST, HUB_IP, …) from the untracked deploy.env, overridden by the environment."""
    env = {}
    if os.environ.get("DEPLOY_ENV"):  # e.g. deploy-gpu.env: one file per setup
        path = path.parent / os.environ["DEPLOY_ENV"]
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("HUB_")})
    return env


def install_agent(config: Config, host: str | None = None, hub_url: str | None = None) -> None:
    """Connect this Mac to the hub: get an agent token, point settings at the hub, run `tabdeck agent`."""
    env = deploy_env()
    host = host or env.get("HUB_HOST")
    hub_url = hub_url or (f"https://{env['HUB_IP']}:{env.get('HUB_PORT') or config.port}" if env.get("HUB_IP") else None)
    if not host or not hub_url:
        raise SystemExit("set HUB_HOST (ssh target) and HUB_IP in deploy.env (see deploy.env.example)")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _certs(config)  # the agent's localhost endpoints use their own certificate
    repo = env.get("HUB_REPO") or f"tabdeck{SUFFIX}"  # the hub's checkout, relative to its home
    out = subprocess.run(["ssh", host, f"cd ~/{repo} && TABDECK_INSTANCE={SUFFIX[1:]} ~/.local/bin/uv run tabdeck "
                                       "issue-agent-token mac"],
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()
    token = out[-1].strip()
    caroot = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True, check=True).stdout.strip()
    path = config.data_dir / "settings.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings.update({"hub_url": hub_url, "agent_token": token, "hub_ca": f"{caroot}/rootCA.pem"})
    write_private(path, json.dumps(settings, indent=2) + "\n")
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    _bootout(LABEL)
    PLIST.write_bytes(plistlib.dumps(build_plist(uv, REPO, config.log_file, command="agent")))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    print("Mac agent running; server sessions open as iTerm tabs. Hub:", hub_url)


CRON_TAG = f"# tabdeck{SUFFIX}"
HOME = f"$HOME/.tabdeck{SUFFIX}"  # the data folder on servers, as cron and sh see it


def merge_crontab(existing: str, reboot: bool = True) -> str:
    """The user's crontab plus the deck lines (save every minute; restore at boot unless systemd does)."""
    lines = [l for l in existing.splitlines() if l.strip()]
    wanted = ([f"@reboot {HOME}/deck-start.sh " + CRON_TAG] if reboot else []) + \
        [f"* * * * * {HOME}/deck-save.sh " + CRON_TAG]
    kept = [l for l in lines if not l.endswith(CRON_TAG)]
    return "\n".join(kept + wanted) + "\n"


def upsert_server(path: Path, entry: dict) -> None:
    try:
        data = json.loads(path.read_text()) if path.exists() else []
    except ValueError:
        data = []
    data = data if isinstance(data, list) else []
    for i, e in enumerate(data):
        if isinstance(e, dict) and e.get("name") == entry["name"]:
            data[i] = entry
            break
    else:
        data.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_server(config: Config, ssh: str, name: str, projects: str = "Projects") -> None:
    """Set up always-on agent sessions on a server: session scripts, crontab, and a Mac gateway entry."""
    from .projects import valid_name
    from .servers import SSH_TARGET
    if not SSH_TARGET.match(ssh) or not valid_name(name) or "'" in projects or projects.startswith("/"):
        raise SystemExit(f"invalid server: name={name!r} ssh={ssh!r} projects={projects!r}")

    def remote(cmd: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", "-o", "ConnectTimeout=10", ssh, cmd], capture_output=True, text=True, **kw)

    d = config.home  # e.g. ~/.tabdeck on the server too
    if remote(f"mkdir -p {d}").returncode != 0:
        raise SystemExit(f"cannot ssh to {ssh} (it must work without a password prompt)")
    scripts = REPO / "scripts"
    subprocess.run(["scp", "-q", str(scripts / "deck-start.sh"), str(scripts / "deck-save.sh"), str(scripts / "agent.sh"),
                    f"{ssh}:{d[2:]}/"], check=True)
    # What the scripts need to know: the tmux session and the agent (deck-start.sh reads it).
    remote(f"printf 'TABDECK_SESSION={config.tmux_session}\\nTABDECK_AGENT={config.agent}\\n' > {d}/instance.env && "
           f"chmod 755 {d}/deck-start.sh {d}/deck-save.sh {d}/agent.sh")
    listed = remote("crontab -l")
    current = listed.stdout if listed.returncode == 0 else ""  # no crontab yet: exit 1
    # On the hub's own server systemd (tabdeck-tmux.service) restores deck at boot, not cron.
    if remote("crontab -", input=merge_crontab(current, reboot=name != config.hub_server)).returncode != 0:
        print("WARNING: could not install the crontab; sessions will not come back after a reboot")
    missing = [tool for tool in ("tmux", config.agent_profile.process)
               if remote(f"PATH=$HOME/.local/bin:$PATH command -v {tool}").returncode != 0]
    if missing:
        print(f"NOTE: {', '.join(missing)} missing on {name}: install it there (and log in to it once).")
    else:
        started = remote(f"sh {d}/deck-start.sh")
        if started.returncode != 0:
            print("WARNING: deck-start.sh failed:", started.stdout + started.stderr)
    host = ssh.split("@")[-1]
    for line in subprocess.run(["ssh", "-G", ssh], capture_output=True, text=True).stdout.splitlines():
        if line.startswith("hostname "):
            host = line.split(None, 1)[1].strip()  # an ~/.ssh/config alias: probe its real address
    upsert_server(config.data_dir / "servers.json", {"name": name, "ssh": ssh, "host": host, "projects": projects})
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    print(f"{name} added: its deck windows open as iTerm tabs whenever it is reachable.")
