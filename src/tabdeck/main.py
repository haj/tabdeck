from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import shutil
import socket
import sys
import time

import uvicorn

from .agents import AgentTokens
from .auth import Auth
from .config import Config, load_config
from .forward import Forwarders
from .intent import Interpreter
from .registry import Registry
from .urls import descendants, needs_forward, parse_lsof, parse_ps, session_urls, url_port
from .web import create_app

log = logging.getLogger("tabdeck")


async def _run(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


async def refresh_urls(registry: Registry, forwarders, config: Config,
                       ps_output: str, lsof_output: str) -> None:
    children = parse_ps(ps_output)
    # Our own forwarders show up in lsof; counting them would switch them off next scan.
    listeners = [l for l in parse_lsof(lsof_output) if l.pid != os.getpid()]
    forward: set[int] = set()
    for s in list(registry.sessions.values()):
        if s.remote:
            continue  # a remote agent's tab: the agent reports its URLs
        pids = descendants(children, s.shell_pid) if s.shell_pid else set()
        pins, hidden = registry.prefs_for(s.cwd)
        urls = session_urls(s.screen_urls, listeners, pids, pins, hidden)
        urls = [u for u in urls if url_port(u) != config.port]
        registry.set_urls(s.session_id, urls)
        if config.netbird_ip:
            # Only expose ports a program in this tab is listening on, never other local services.
            owned = {l.port for l in listeners if l.pid in pids}
            for u in urls:
                port = url_port(u)
                if port in owned and needs_forward(port, listeners, config.netbird_ip):
                    forward.add(port)
    if forwarders is not None:
        await forwarders.ensure(forward)


async def poll_once(registry: Registry, bridge) -> None:
    registry.update(await bridge.snapshot(), time.time())
    registry.set_active(await bridge.active_session())
    registry.set_iterm_ok(True)


def make_source(config: Config):
    if config.source == "tmux":
        from .composite import CompositeSource
        from .remote_source import RemoteSource
        from .tmux_source import TmuxSource
        return CompositeSource(TmuxSource(server=config.hub_server, session=config.tmux_session),
                               {"mac": RemoteSource("mac")},
                               hub_server=config.hub_server)
    from .iterm_bridge import ItermBridge
    return ItermBridge()


def make_transcriber(config: Config):
    if config.stt_url:
        from .stt import WhisperTranscriber
        return WhisperTranscriber(config.stt_url, config.stt_model)  # an OpenAI-compatible Whisper service
    if sys.platform != "darwin":
        return None  # the hub is a pure server: clients send text
    from .voice import Transcriber
    return Transcriber(config.whisper_model)


async def poll_loop(registry: Registry, bridge) -> None:
    while True:
        try:
            if not bridge.connected:
                await bridge.connect()
                log.info("connected to iTerm2")
            await poll_once(registry, bridge)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - iTerm quit, API disabled, connection dropped
            log.warning("session source unavailable: %s", e)
            await bridge.reset()
            registry.set_iterm_ok(False)
            await asyncio.sleep(3)


async def ports_loop(registry: Registry, forwarders, config: Config) -> None:
    if shutil.which("lsof") is None:
        log.info("lsof not available; app URL detection off")
        return
    while True:
        try:
            ps = await _run("ps", "-Ao", "pid=,ppid=")
            lsof = await _run("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pn")
            await refresh_urls(registry, forwarders, config, ps, lsof)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("url refresh failed")
        await asyncio.sleep(3)


def private_address(ip: str) -> bool:
    """Private, VPN (NetBird/Tailscale 100.64.0.0/10) or loopback: the only extra addresses the hub listens on."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not addr.is_unspecified and (addr.is_private or addr.is_loopback or addr in ipaddress.ip_network("100.64.0.0/10"))


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


async def run(config: Config) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    registry = Registry(config.state_file)
    bridge = make_source(config)
    auth = Auth(config.tokens_file)
    transcriber = make_transcriber(config)
    forwarders = Forwarders(config.netbird_ip) if config.netbird_ip else None
    interpreter = Interpreter(url=config.ollama_url, model=config.intent_model, api=config.llm_api,
                              key=config.llm_key or None, timeout=config.intent_timeout,
                              summary_timeout=config.summary_timeout, assistant=config.assistant_name,
                              agent=config.agent_profile.product)
    app = create_app(registry=registry, bridge=bridge, auth=auth, transcriber=transcriber, config=config,
                     interpreter=interpreter,
                     agents=AgentTokens(config.data_dir / "agents.json"), remotes=getattr(bridge, "remotes", {}))

    sockets = [_bind("127.0.0.1", config.port)]
    if config.netbird_ip and not private_address(config.netbird_ip) and not config.allow_public:
        log.error("refusing to listen on %s: not a private or VPN address (TabDeck is not meant for the internet); "
                  "serving localhost only. To listen there anyway, set \"allow_public\": true in %s/settings.json",
                  config.netbird_ip, config.data_dir)
    elif config.netbird_ip:
        try:
            sockets.append(_bind(config.netbird_ip, config.port))
        except OSError as e:
            log.warning("cannot bind NetBird IP %s (%s); serving localhost only", config.netbird_ip, e)
    else:
        log.warning("NetBird IP not found; serving localhost only")

    server = uvicorn.Server(uvicorn.Config(
        app, ssl_certfile=str(config.cert_file), ssl_keyfile=str(config.key_file),
        log_level="warning"))
    if transcriber is not None:
        asyncio.get_running_loop().run_in_executor(None, transcriber.warm)
    asyncio.get_running_loop().run_in_executor(None, interpreter.warm)
    log.info("TabDeck on https://localhost:%d%s", config.port,
             f" and https://{config.netbird_ip}:{config.port}" if len(sockets) > 1 else "")
    await asyncio.gather(server.serve(sockets=sockets), poll_loop(registry, bridge),
                         ports_loop(registry, forwarders, config))


async def run_agent(config: Config) -> None:
    import httpx
    from .agent import Agent, agent_loop
    from .agent_web import create_agent_app
    from .gateways import GatewayManager, gateway_loop
    from .iterm_bridge import ItermBridge
    from .servers import load_servers

    settings = config.settings
    hub, token, cafile = settings.get("hub_url"), settings.get("agent_token"), settings.get("hub_ca")
    if not hub or not token:
        raise SystemExit(f"hub_url and agent_token missing in {config.data_dir}/settings.json (run: make agent)")
    registry, bridge = Registry(config.state_file), ItermBridge()
    servers = load_servers(config.data_dir / "servers.json")

    def servers_changed() -> None:
        registry.version += 1  # the agent sends the hub a fresh snapshot (with servers)
    bridge.session = config.tmux_session
    gateways = GatewayManager(servers, bridge, on_change=servers_changed, session=config.tmux_session,
                              home=config.home) if servers else None
    agent = Agent(registry, bridge, config.projects_dir, netbird_ip=config.netbird_ip, gateways=gateways,
                  hub_server=config.hub_server, mac_tabs=config.mac_tabs, profile=config.agent_profile,
                  home=config.home, session=config.tmux_session)
    client = httpx.AsyncClient(verify=cafile or True, headers={"Authorization": f"Bearer {token}"}, timeout=20)

    async def utterance(body: dict) -> dict:
        r = await client.post(hub.rstrip("/") + "/api/utterance", json=body)
        r.raise_for_status()
        return r.json()

    transcriber = make_transcriber(config)
    from .web import assistant_hint
    app = create_agent_app(agent, transcriber, utterance, assistant_hint(config.assistant_name, config.wake_word),
                           config=config)
    server = uvicorn.Server(uvicorn.Config(app, ssl_certfile=str(config.cert_file),
                                           ssl_keyfile=str(config.key_file), log_level="warning"))
    forwarders = Forwarders(config.netbird_ip) if config.netbird_ip else None
    if transcriber is not None:
        asyncio.get_running_loop().run_in_executor(None, transcriber.warm)
    log.info("TabDeck agent on https://localhost:%d → hub %s", config.port, hub)
    if gateways is not None:
        log.info("tmux gateways: %s", ", ".join(s.name for s in servers))
    await asyncio.gather(server.serve(sockets=[_bind("127.0.0.1", config.port)]), poll_loop(registry, bridge),
                         ports_loop(registry, forwarders, config), agent_loop(agent, hub, token, cafile),
                         *([gateway_loop(gateways, bridge)] if gateways is not None else []))


def move(config: Config, project: str, server_name: str, session: str | None) -> None:
    from pathlib import Path
    from .move import move_session
    from .servers import load_servers
    if config.agent != "claude":
        raise SystemExit("tabdeck move carries Claude Code conversations; this setup runs " + config.agent_profile.name)
    server = next((s for s in load_servers(config.data_dir / "servers.json") if s.name == server_name), None)
    if server is None:
        raise SystemExit(f"unknown server {server_name!r}: add it first with `make server HOST=… NAME={server_name}`")
    print(f"Moving {project} to {server.name}…")
    r = move_session(project, server, session=session, projects_dir=config.projects_dir,
                     claude_projects=Path.home() / ".claude" / "projects", tmux_session=config.tmux_session)
    print(f"Resumed conversation {r['session']} on {r['server']} in {r['path']} (deck window {r['window']}).")
    print("Its iTerm tab opens by itself. The Mac session is still running: close it once the new tab works.")
    if r["active"]:
        print("NOTE: the Mac session wrote to its conversation in the last 2 minutes. Anything it does from now on "
              "is not in the copy; stop using it before carrying on in the new tab.")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="tabdeck")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the service")
    sub.add_parser("agent", help="run the Mac agent (reports iTerm tabs to the hub)")
    sub.add_parser("install-agent", help="connect this Mac to the hub and run the agent at login")
    sub.add_parser("install", help="certs, Claude Code hook, launchd agent")
    sub.add_parser("uninstall", help="remove hook and launchd agent")
    sub.add_parser("install-widget", help="build and start the floating Deck widget")
    sub.add_parser("export-projects", help="print project git remotes as JSON (to seed the hub)")
    srv = sub.add_parser("install-server", help="always-on agent sessions on a server, shown as iTerm tabs")
    srv.add_argument("ssh", help="ssh target, e.g. user@host or an ~/.ssh/config alias")
    srv.add_argument("name", help="short name, e.g. trading")
    srv.add_argument("--projects", default="Projects", help="projects folder relative to home (default Projects)")
    mv = sub.add_parser("move", help="move a Mac project's Claude session to a server (the Mac one keeps running)")
    mv.add_argument("project", help="folder name in ~/Projects, e.g. myproject")
    mv.add_argument("server", help="a server name from <data dir>/servers.json")
    mv.add_argument("--session", help="conversation id (default: the project's most recent one)")
    op = sub.add_parser("open", help="on the hub server: start a session in a folder (default: this one)")
    op.add_argument("path", nargs="?", default=".", help="a folder under your home, e.g. ~/work/api")
    op.add_argument("--task", default="", help="first message to send once the agent is ready")
    pr = sub.add_parser("pair", help="show a pairing QR code and link for a phone or browser")
    pr.add_argument("--open", action="store_true", help="also open it here, pairing this Mac's browser")
    st = sub.add_parser("setup", help="set up a hub, this Mac and the widget (asks; flags skip questions)")
    st.add_argument("--instance", help="name of a second setup next to the first, e.g. gpu")
    st.add_argument("--host", help="ssh target of the hub server")
    st.add_argument("--ip", help="the hub's private-network IP")
    st.add_argument("--agent", choices=["claude", "opencode"])
    st.add_argument("--assistant", help="assistant name, e.g. Jarvis")
    st.add_argument("--wake", help='wake word, e.g. jarvis or "hey friday"')
    st.add_argument("--port", type=int)
    st.add_argument("--session", help="tmux session on servers (default deck, or the instance name)")
    st.add_argument("--hub-name", dest="hub_name", help="the hub server's short name (default: its hostname)")
    st.add_argument("--fqdn", help="the hub's private-network name, added to its certificate")
    st.add_argument("--ods", action=argparse.BooleanOptionalAction, default=None, help="the hub runs ODS: use its model, Whisper and voice")
    st.add_argument("--mac-tabs", dest="mac_tabs", action=argparse.BooleanOptionalAction, default=None,
                    help="also report this Mac's own iTerm tabs")
    st.add_argument("--yes", action="store_true", help="write the files and run every step without asking")
    tok = sub.add_parser("issue-agent-token", help="create a token for an agent (run on the hub)")
    tok.add_argument("name")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        asyncio.run(run(load_config()))
    elif args.cmd == "agent":
        asyncio.run(run_agent(load_config()))
    elif args.cmd == "install-agent":
        from .install import install_agent
        install_agent(load_config())
    elif args.cmd == "open":
        from .pair import open_folder
        sid = open_folder(load_config(), args.path, args.task)
        print(f"Session {sid} started in {args.path}: it opens as an iTerm tab on your Mac.")
    elif args.cmd == "pair":
        from .pair import pair
        pair(load_config(), open_here=args.open)
    elif args.cmd == "setup":
        from .setup import setup
        setup({k: v for k, v in vars(args).items() if k != "cmd"})
    elif args.cmd == "move":
        move(load_config(), args.project, args.server, args.session)
    elif args.cmd == "install-server":
        from .install import install_server
        install_server(load_config(), args.ssh, args.name, args.projects)
    elif args.cmd == "install":
        from .install import install
        install(load_config())
    elif args.cmd == "issue-agent-token":
        from .agents import AgentTokens
        print(AgentTokens(load_config().data_dir / "agents.json").issue(args.name))
    elif args.cmd == "export-projects":
        from .projects import project_remotes
        print(json.dumps(project_remotes(load_config().projects_dir), indent=2))
    elif args.cmd == "install-widget":
        from .install import install_widget
        install_widget(load_config())
    else:
        from .install import uninstall
        uninstall(load_config())
