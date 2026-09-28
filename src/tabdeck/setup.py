"""`tabdeck setup`: ask a few questions (or take flags), detect what can be detected over ssh, and write the
deploy file, this Mac's settings and server list. Then optionally deploy the hub and install the Mac agent
and widget. Nothing about your machines lives in the repo: answers go to untracked files."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import detect_netbird_ip
from .files import write_private
from .profiles import AGENTS, INSTANCE_NAME
from .projects import valid_name
from .servers import SSH_TARGET

REPO = Path(__file__).resolve().parents[2]
TABDECK = str(Path(sys.executable).parent / "tabdeck")  # this environment's `tabdeck` command
WAKE = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,30}$")
SESSION = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
FQDN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


@dataclass
class Answers:
    instance: str  # "" for the default setup, else e.g. "ods" (~/.tabdeck-ods, its own launchd jobs)
    host: str  # ssh target of the hub server
    ip: str  # its private-network (NetBird, Tailscale, WireGuard) IP
    agent: str  # claude or opencode
    assistant: str  # e.g. Jarvis
    wake: str  # e.g. jarvis, "hey ods"
    port: int
    session: str  # tmux session on servers
    ods: bool  # the hub server runs ODS: use its model, Whisper and Kokoro
    hub_name: str  # the hub server's short name (its sessions show as that server)
    mac_tabs: bool  # the Mac agent also reports the Mac's own iTerm tabs
    fqdn: str = ""  # optional private-network name, added to the hub's certificate


def validate(a: Answers) -> list[str]:
    problems = []
    if not SSH_TARGET.match(a.host):
        problems.append(f"ssh target {a.host!r} should look like user@host or an ~/.ssh/config alias")
    try:
        if ipaddress.ip_address(a.ip).version != 4:
            raise ValueError
    except ValueError:
        problems.append(f"IP {a.ip!r} is not an IPv4 address")
    if a.agent not in AGENTS:
        problems.append(f"agent {a.agent!r} must be one of {', '.join(AGENTS)}")
    if a.instance and not INSTANCE_NAME.match(a.instance):
        problems.append(f"instance {a.instance!r}: lowercase letters, digits and dashes")
    if not 1024 <= a.port <= 65535:
        problems.append(f"port {a.port} must be between 1024 and 65535")
    if not WAKE.match(a.wake) or len(a.wake.split()) > 3:
        problems.append(f"wake word {a.wake!r}: one to three words")
    if not WAKE.match(a.assistant):
        problems.append(f"assistant name {a.assistant!r}: letters and spaces")
    if not SESSION.match(a.session):
        problems.append(f"tmux session {a.session!r}: letters, digits, - and _")
    if not valid_name(a.hub_name):
        problems.append(f"hub name {a.hub_name!r}: letters, digits, . - _")
    if a.fqdn and not FQDN.match(a.fqdn):
        problems.append(f"name {a.fqdn!r} is not a host name")
    return problems


def deploy_file(a: Answers, repo: str) -> str:
    return f"{repo}/deploy{'-' + a.instance if a.instance else ''}.env"


def plan_files(a: Answers, repo: str, data_dir: str) -> dict[str, str]:
    """Every file setup writes, with its content. Existing settings (tokens, voice) and servers are kept."""
    env = {"HUB_HOST": a.host, "HUB_IP": a.ip, "HUB_AGENT": a.agent, "HUB_PORT": str(a.port),
           "HUB_SESSION": a.session, "HUB_ASSISTANT": a.assistant, "HUB_WAKE": a.wake}
    if a.fqdn:
        env["HUB_FQDN"] = a.fqdn
    if a.instance:
        env["TABDECK_INSTANCE"] = a.instance
    if a.ods:
        env["HUB_ODS"] = "1"
    deploy = "# Written by `tabdeck setup`. Untracked: machine addresses never go in the repo.\n" + \
        "".join(f"{k}={shlex.quote(v)}\n" for k, v in env.items())
    settings = _load(Path(data_dir) / "settings.json", {})
    settings.update({"agent": a.agent, "assistant_name": a.assistant, "wake_word": a.wake, "port": a.port,
                     "tmux_session": a.session, "hub_server": a.hub_name, "mac_tabs": a.mac_tabs})
    servers = [s for s in _load(Path(data_dir) / "servers.json", []) if isinstance(s, dict) and s.get("name") != a.hub_name]
    servers.append({"name": a.hub_name, "ssh": a.host, "host": a.ip, "projects": "Projects"})
    return {deploy_file(a, repo): deploy,
            f"{data_dir}/settings.json": json.dumps(settings, indent=2) + "\n",
            f"{data_dir}/servers.json": json.dumps(servers, indent=2) + "\n"}


def _load(path: Path, default):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def write_files(files: dict[str, str]) -> None:
    for path, content in files.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_private(Path(path), content)  # settings hold the agent token; deploy files hold addresses


def detect_hub(host: str, run=subprocess.run) -> dict:
    """The hub server's short name, private-network IP (from NetBird, if it runs there) and whether ODS is there."""
    def ssh(cmd: str):
        return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, cmd], capture_output=True, text=True,
                   stdin=subprocess.DEVNULL)  # never swallow answers the user typed ahead
    name = ssh("hostname -s || hostname")
    if name.returncode != 0:
        raise SystemExit(f"cannot ssh to {host} without a password prompt; set up an ssh key first")
    found = {"hub_name": name.stdout.strip().split(".")[0].lower() or "hub"}
    ip = detect_netbird_ip(ssh("netbird status 2>/dev/null").stdout)
    if ip:
        found["ip"] = ip
    found["ods"] = ssh("test -f ~/ods/.env").returncode == 0
    return found


def _reader(ask):
    def read(prompt: str) -> str:
        try:
            return ask(prompt)
        except EOFError:
            raise SystemExit("setup needs answers: run it in a terminal, or pass every choice as a flag") from None
    return read


def run_setup(opts: dict, ask=input, run=subprocess.run, say=print) -> Answers:
    """Fill the answers from flags, detection and questions (Enter keeps the suggestion)."""
    read = _reader(ask)

    def q(label: str, suggestion: str) -> str:
        return (read(f"{label} [{suggestion}]: ").strip() or suggestion) if suggestion else read(f"{label}: ").strip()

    def yes(label: str, default: bool) -> bool:
        answer = read(f"{label} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    instance = opts.get("instance")
    if instance is None:
        instance = q("Instance name (Enter for the default setup; e.g. ods for a second one)", os.environ.get("TABDECK_INSTANCE", ""))
    host = opts.get("host") or q("Hub server ssh target (user@host or ~/.ssh/config alias)", "")
    say(f"Checking {host} over ssh…")
    found = detect_hub(host, run=run)
    ip = opts.get("ip") or q("Hub private-network IP (NetBird/Tailscale/WireGuard)", found.get("ip", ""))
    ods = opts["ods"] if opts.get("ods") is not None else (found["ods"] and yes("ODS found on the hub: use its model, Whisper and voice?", True))
    agent = opts.get("agent") or q("Agent in each session (claude or opencode)", "opencode" if ods else "claude")
    assistant = opts.get("assistant") or q("Assistant name", "ODS" if ods and agent == "opencode" else "Jarvis")
    wake = opts.get("wake") or q("Wake word", "hey ods" if assistant.upper() == "ODS" else assistant.lower())
    port_text = str(opts.get("port") or q("Port", "8766" if instance else "8765"))
    port = int(port_text) if port_text.isdigit() else -1  # validate() reports a bad one
    session = opts.get("session") or instance or "deck"
    mac_tabs = opts["mac_tabs"] if opts.get("mac_tabs") is not None else not instance
    a = Answers(instance=instance, host=host, ip=ip, agent=agent, assistant=assistant, wake=wake, port=port,
                session=session, ods=bool(ods), hub_name=opts.get("hub_name") or found["hub_name"],
                mac_tabs=mac_tabs, fqdn=opts.get("fqdn") or "")
    problems = validate(a)
    if problems:
        raise SystemExit("setup stopped:\n  " + "\n  ".join(problems))
    return a


def setup(opts: dict, ask=input, run=subprocess.run, say=print) -> None:
    a = run_setup(opts, ask=ask, run=run, say=say)
    read = _reader(ask)
    data_dir = str(Path.home() / f".tabdeck{'-' + a.instance if a.instance else ''}")
    files = plan_files(a, str(REPO), data_dir)
    say("\nSetup:")
    say(f"  hub {a.hub_name} ({a.host}, {a.ip}:{a.port}), agent {a.agent}, assistant {a.assistant!r} ('{a.wake}')"
        + (", ODS" if a.ods else "") + (f", instance {a.instance}" if a.instance else ""))
    for path in files:
        say(f"  writes {path}")
    if not opts.get("yes") and read("Write these files? [Y/n]: ").strip().lower().startswith("n"):
        raise SystemExit("nothing written")
    write_files(files)
    env = {**os.environ, "DEPLOY_ENV": Path(deploy_file(a, str(REPO))).name}
    if a.instance:
        env["TABDECK_INSTANCE"] = a.instance
    steps = [("Deploy the hub now", ["sh", str(REPO / "scripts" / "deploy-hub.sh")]),
             ("Install the Mac agent (iTerm tabs, voice)", [TABDECK, "install-agent"]),
             ("Build and start the widget", [TABDECK, "install-widget"])]
    for label, cmd in steps:
        if opts.get("yes") or not read(f"{label}? [Y/n]: ").strip().lower().startswith("n"):
            if subprocess.run(cmd, env=env, cwd=REPO).returncode != 0:
                raise SystemExit(f"{label.lower()} failed; fix the problem and run `tabdeck setup` again")
    say(f"\nDone. Web page: https://{a.ip}:{a.port} (pair it from this Mac). Say \"{a.wake}, what's going on?\"")
