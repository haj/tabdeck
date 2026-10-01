from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

import qrcode
import qrcode.image.svg
import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect
from urllib.parse import urlsplit

from .auth import Auth, is_local, is_local_request
from . import settings as live_settings
from .commands import Command, normalize, parse, resolve, strip_wake, wake_pattern
from .config import Config, load_config
from .projects import all_projects, clone, load_remotes, valid_name
from .remote_source import AgentError, AgentOffline, AgentTimeout
from .intent import llm_action
from .registry import Registry
from .speech import chunk, clean_for_speech
from .status import SHELLS, SessionState, Status
from .transcript import last_assistant_text
from .urls import phone_href

log = logging.getLogger("tabdeck.voice")
COOKIE = "tabdeck_token"
KEYS = {"enter": "\r", "escape": "\x1b", "1": "1", "2": "2", "3": "3"}
STATIC = Path(__file__).parent / "static"
SCREEN_LINES = 60
TRUST_PROMPT = "Yes, I trust this folder"
SUMMARIZE_OVER = 250  # characters of spoken reply
NOT_PAIRED = (
    "<!doctype html><meta name=viewport content='width=device-width'><title>TabDeck</title>"
    "<p style='font:18px system-ui;padding:24px'>This device is not paired. On the Mac, open "
    "TabDeck, tap <b>Pair phone</b>, and scan the QR code.</p>"
)


YES = {"yes", "yeah", "yep", "yup", "correct", "right", "sure", "ok", "okay", "do it", "go ahead",
       "send it", "yes please", "yes send it", "yeah send it", "thats right", "exactly"}
NO = {"no", "nope", "nah", "wrong", "no thanks", "dont", "dont send it", "never mind", "nevermind", "cancel"}


TELL_NO_MESSAGE = re.compile(r"^(?:tell|ask|write to|send to)\s+\w+(?:\s+\d+)?$")
# Words a finished sentence practically never ends on ("on", "in", "that", "this" can: "turn it on", "fix that").
DANGLING = {"the", "a", "an", "to", "with", "and", "or", "of", "for", "from", "into", "about",
            "my", "your", "its", "uh", "um"}


def _dangling(message: str) -> bool:
    words = normalize(message).split()
    return bool(words) and words[-1] in DANGLING


def _yes_no(text: str) -> bool | None:
    norm = normalize(text)
    if norm in YES:
        return True
    if norm in NO:
        return False
    return None


MAX_TTS = 1000
VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z0-9_]{1,30}$")


async def _kokoro_voices(url: str) -> list[str]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url.rstrip("/") + "/v1/audio/voices")
        r.raise_for_status()
        voices = r.json().get("voices", [])
        return [v if isinstance(v, str) else str(v.get("id", "")) for v in voices]


async def _kokoro(url: str, text: str, voice: str) -> bytes:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url.rstrip("/") + "/v1/audio/speech",
                              json={"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3"})
        r.raise_for_status()
        return r.content


class VoiceBody(BaseModel):
    voice: str


def wake_phrase(name: str, wake_word: str) -> str:
    """How to address the assistant, for display: "Jarvis", "Hey Friday"."""
    words = [name if w.lower() == name.lower() else w.capitalize() for w in wake_word.split()]
    return " ".join(words) or name


def assistant_hint(name: str, wake_word: str) -> str:
    """How Whisper should expect the assistant to be addressed: "Jarvis", or 'Friday ("Hey Friday")'."""
    if " ".join(wake_word.lower().split()) == name.lower():
        return name
    words = [name if w.lower() == name.lower() else w.capitalize() for w in wake_word.split()]
    return f'{name} ("{" ".join(words)}")'


class TextBody(BaseModel):
    text: str


class KeyBody(BaseModel):
    key: str


class UrlBody(BaseModel):
    action: str
    url: str = ""


class ProjectBody(BaseModel):
    project: str
    task: str = ""
    where: str = ""  # "" (or the hub's own name): on the hub; "mac": in iTerm on the Mac; else a server name
    create: bool = False  # a new project: make its folder in the projects folder first
    path: str = ""  # instead of a project: any existing folder under the hub user's home, e.g. ~/work/api


class UtteranceBody(BaseModel):
    text: str
    selected: str = ""
    wake: str = "0"
    followup: str = "0"
    compose: str = "0"
    confirming: str = "0"
    final: str = "0"
    length: str = "normal"


class CommandBody(BaseModel):
    text: str
    selected: str = ""


def create_app(*, registry: Registry, bridge, auth: Auth, transcriber, config: Config,
               clock: Callable[[], float] = time.time, interpreter=None,
               ready_timeout: float = 30.0, ready_settle: float = 1.0, git_clone=None,
               agents=None, remotes=None, tts_fetch=None, tts_voices=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    git_clone = git_clone or clone
    tts_fetch = tts_fetch or _kokoro
    tts_voices = tts_voices or _kokoro_voices
    shared_voice = {"name": config.tts_voice}  # shared voice; changed from the widget's Voice menu
    remotes = remotes or {}
    agent = config.agent_profile  # the coding agent sessions run (Claude Code or OpenCode)
    wake_re = wake_pattern(config.wake_word)
    whisper_hint = assistant_hint(config.assistant_name, config.wake_word)
    base_transcriber = transcriber  # what the hub started with (Whisper on a Mac, or none on a server)

    def apply_live(clean: dict) -> None:
        """Use changed settings at once: every request after this sees them (the handlers read these names)."""
        nonlocal config, wake_re, whisper_hint, transcriber
        config = live_settings.apply(config, clean)
        wake_re = wake_pattern(config.wake_word)
        whisper_hint = assistant_hint(config.assistant_name, config.wake_word)
        shared_voice["name"] = config.tts_voice
        if hasattr(interpreter, "configure"):
            interpreter.configure(url=config.ollama_url, model=config.intent_model, api=config.llm_api,
                                  key=config.llm_key or None, timeout=config.intent_timeout,
                                  summary_timeout=config.summary_timeout, assistant=config.assistant_name)
        if config.stt_url:
            from .stt import WhisperTranscriber
            transcriber = WhisperTranscriber(config.stt_url, config.stt_model)
        else:
            transcriber = base_transcriber
        registry.version += 1  # push the new name and wake phrase to every page and widget

    def project_names() -> list[str]:
        """Local project folders plus projects the hub can clone from their git remote."""
        return all_projects(config.projects_dir, load_remotes(config.projects_file))

    def mac_servers() -> list[dict]:
        """Servers the Mac keeps tmux gateways to (other than this hub): {name, online, projects}."""
        mac = remotes.get("mac")
        return [v for v in getattr(mac, "servers", []) if v["name"] != config.hub_server] if mac else []

    def server_names() -> list[str]:
        return [config.hub_server] + [v["name"] for v in mac_servers()]

    def command_projects() -> list[str]:
        """Project names Jarvis can recognise: the hub's, the Mac's and the servers'."""
        names = project_names()
        mac = remotes.get("mac")
        for p in (mac.projects if mac else []) + [p for v in mac_servers() for p in v["projects"]]:
            if p not in names:
                names.append(p)
        return names

    async def act(awaitable):
        """Run a source command, turning remote-agent failures into clear HTTP errors."""
        try:
            return await awaitable
        except KeyError:
            raise HTTPException(404, "That tab is gone") from None
        except AgentOffline:
            raise HTTPException(409, "Your Mac is offline.") from None
        except AgentTimeout:
            raise HTTPException(504, "Your Mac didn't answer.") from None
        except AgentError as e:
            raise HTTPException(409, str(e)) from None
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """Browsers re-check the page's files on each load (a cheap 304 when unchanged), so a deploy shows at once."""
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def host_of(conn) -> str:
        return conn.client.host if conn.client else ""

    def same_origin(conn) -> bool:
        # Browsers send Origin on cross-site requests and WebSockets; without this check any
        # website open on the Mac could use the localhost trust below.
        origin = conn.headers.get("origin")
        return origin is None or urlsplit(origin).netloc == conn.headers.get("host")

    def authed(conn) -> bool:
        if not same_origin(conn):
            return False
        bearer = conn.headers.get("authorization", "")
        if agents is not None and bearer.startswith("Bearer ") and agents.verify(bearer[7:]):
            return True
        return is_local_request(conn) or auth.valid(conn.cookies.get(COOKIE))

    def require(request: Request) -> None:
        if not authed(request):
            raise HTTPException(401, "Not paired")

    def require_local(request: Request) -> None:
        if not (is_local_request(request) and same_origin(request)):
            raise HTTPException(403, "Only from this machine")

    def require_owner(request: Request) -> None:
        """Who may pair and unpair devices: this machine, your Mac agent, or an already paired device (it can
        type into your terminals anyway, so adding a device gives it nothing new; Unpair all removes every one)."""
        if not authed(request):
            raise HTTPException(403, "Pair this device first: on your Mac run `tabdeck pair`, "
                                     "or use the widget menu → Pair a phone…")

    def session(sid: str) -> SessionState:
        s = registry.sessions.get(sid)
        if s is None:
            raise HTTPException(404, "That tab is gone")
        return s

    def session_json(s: SessionState, name: str, local: bool) -> dict:
        def href(u: str) -> str:
            if s.remote:  # a remote agent's tab: its localhost is that machine, not the hub
                return phone_href(u, s.url_host) if s.url_host else u
            return u if local or not config.netbird_ip else phone_href(u, config.netbird_ip)
        return {"id": s.session_id, "name": name, "title": s.tab_title, "cwd": s.cwd,
                "status": s.status.value, "offline": s.offline, "since": s.status_since, "seen": s.seen,
                "message": s.message, "server": s.server, "urls": [{"url": u, "href": href(u)} for u in s.urls]}

    def state_json(local: bool) -> dict:
        names = registry.names()
        return {"type": "state", "iterm_connected": registry.iterm_ok, "is_local": local, "can_pair": True,
                "active": registry.active,
                "tts_voice": shared_voice["name"],
                "assistant_name": config.assistant_name,
                "wake_phrase": wake_phrase(config.assistant_name, config.wake_word),
                "source": config.source,
                "servers": [{"name": config.hub_server, "online": True}]
                + [{"name": v["name"], "online": v["online"]} for v in mac_servers()],
                "sessions": [session_json(s, names[s.session_id], local) for s in registry.ordered()]}

    catch_up = {"since": clock() - 7200}  # first catch-up covers the last two hours

    def digest(action: dict) -> dict:
        """Turn a catch_up action into a spoken digest of activity since the last one."""
        if action.get("type") != "catch_up":
            return action
        events = registry.activity(since=catch_up["since"])
        catch_up["since"] = clock()
        if not events:
            return {"type": "speak", "text": "Nothing new since your last catch-up."}
        phrases = {"done": "{tab} finished: {text}", "needs_you": "{tab} needed you: {text}",
                   "sent": "You sent {tab}: {text}", "approved": "You approved {tab}.",
                   "denied": "You denied {tab}.", "always": "You always-allowed {tab}."}
        lines = []
        for e in events[-12:]:
            text = e["text"].strip().rstrip(".") + "." if e["text"].strip() else ""
            lines.append(phrases.get(e["kind"], "{tab}: {text}").format(tab=e["tab"], text=text).strip())
        return {"type": "speak", "text": " ".join(lines)}

    def command(text: str, selected: str) -> dict:
        action = resolve(parse(text), registry.ordered(), registry.names(), selected or None,
                         project_names())
        return {"text": text, "action": digest(action)}

    # ---- page & pairing ----
    @app.get("/")
    def index(request: Request):
        if not authed(request):
            return HTMLResponse(NOT_PAIRED, status_code=401)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/pair")
    def pair_redeem(code: str = ""):
        token = auth.redeem(code, clock())
        if not token:
            return HTMLResponse("<p style='font:18px system-ui;padding:24px'>This pairing link "
                                "expired or was used. Create a new one on the Mac.</p>", status_code=403)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, max_age=31536000, httponly=True, secure=True, samesite="lax")
        return resp

    @app.post("/api/pair")
    def pair_new(request: Request):
        require_owner(request)
        if not config.netbird_ip:
            raise HTTPException(409, "NetBird IP not detected; is NetBird connected?")
        url = f"https://{config.netbird_ip}:{config.port}/pair?code={auth.new_pairing_code(clock())}"
        svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).to_string(encoding="unicode")
        return {"url": url, "svg": svg}

    @app.post("/api/revoke")
    def revoke(request: Request):
        require_owner(request)
        auth.revoke_all()
        return {"ok": True}

    # ---- state ----
    @app.get("/api/state")
    def get_state(request: Request):
        require(request)
        return state_json(is_local_request(request))

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not authed(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        local = is_local_request(websocket)

        async def sender():
            last, idle = -1, 0.0
            while True:
                if registry.version != last:
                    last = registry.version
                    await websocket.send_json(state_json(local))
                    idle = 0.0
                elif idle >= 15:
                    await websocket.send_json({"type": "ping"})
                    idle = 0.0
                await asyncio.sleep(0.3)
                idle += 0.3

        task = asyncio.create_task(sender())
        try:
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            task.cancel()

    # ---- session actions ----
    @app.post("/api/sessions/{sid}/send")
    async def send(sid: str, body: TextBody, request: Request):
        require(request)
        s = session(sid)
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Empty reply")
        if s.job_name in SHELLS:
            # Typed into a bare shell, a sentence would run as a command.
            raise HTTPException(409, "Claude isn't running in that tab, so I didn't type anything.")
        await act(bridge.send_text(sid, text))
        registry.mark(sid, Status.WORKING, "", clock())
        registry.record("sent", sid, text, clock())
        return {"ok": True}

    @app.post("/api/sessions/{sid}/keys")
    async def keys(sid: str, body: KeyBody, request: Request):
        require(request)
        s = session(sid)
        if body.key not in KEYS:
            raise HTTPException(400, "Unknown key")
        await act(bridge.send_keys(sid, KEYS[body.key]))
        if s.status is Status.NEEDS_YOU:
            kind = {"enter": "approved", "1": "approved", "2": "always", "escape": "denied"}.get(body.key)
            if kind:
                registry.record(kind, sid, "", clock())
        if body.key == "escape" and s.hook_seen:
            registry.mark(sid, Status.DONE, "Stopped. Waiting for you.", clock())
        elif body.key in ("enter", "1", "2") and s.status is Status.NEEDS_YOU:
            registry.mark(sid, Status.WORKING, "", clock())
        return {"ok": True}

    @app.post("/api/sessions/{sid}/focus")
    async def focus(sid: str, request: Request):
        require(request)
        session(sid)
        await act(bridge.focus(sid))
        return {"ok": True}

    @app.post("/api/sessions/{sid}/seen")
    def seen(sid: str, request: Request):
        require(request)
        session(sid)
        registry.mark_seen(sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/reply")
    async def reply(sid: str, request: Request):
        require(request)
        s = session(sid)
        # A remote agent's tab: only that machine can read its transcript.
        text = await act(bridge.reply_text(sid)) if hasattr(bridge, "reply_text") else None
        if text is None:
            text = last_assistant_text(s.transcript_path) if s.transcript_path else ""
        # "text" is shown as written (line breaks, lists, code); "chunks" are read aloud (no markdown).
        return {"text": text or "", "chunks": chunk(clean_for_speech(text), 5) if text else []}

    @app.get("/api/sessions/{sid}/screen")
    def screen(sid: str, request: Request):
        require(request)
        return {"text": "\n".join(session(sid).screen_text.splitlines()[-SCREEN_LINES:])}

    @app.post("/api/sessions/{sid}/urls")
    def urls(sid: str, body: UrlBody, request: Request):
        require(request)
        session(sid)
        if body.action == "pin":
            registry.pin_url(sid, body.url)
        elif body.action == "hide":
            registry.hide_url(sid, body.url)
        elif body.action == "reset":
            registry.reset_urls(sid)
        else:
            raise HTTPException(400, "Unknown action")
        return {"ok": True}

    # ---- voice (tts_url, e.g. Kokoro; proxied: an HTTPS page may not fetch plain-HTTP audio) ----
    @app.get("/api/tts")
    async def tts(request: Request, text: str = ""):
        require(request)
        text = text.strip()
        if not text or len(text) > MAX_TTS:
            raise HTTPException(400, "Text missing or too long")
        try:
            audio = await tts_fetch(config.tts_url, text, shared_voice["name"])
        except Exception as e:  # noqa: BLE001 - voice server down: clients fall back to their own voice
            raise HTTPException(502, f"Voice unavailable: {e}") from e
        return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/tts/voices")
    async def list_voices(request: Request):
        """English voices of the hub's voice server, for the widget's and page's voice pickers."""
        require(request)
        if not config.tts_url:
            return {"voices": []}
        try:
            ids = await tts_voices(config.tts_url)
        except Exception as e:  # noqa: BLE001 - voice server down: an empty list, the picker says so
            log.info("voice list unavailable: %s", e)
            return {"voices": []}
        return {"voices": sorted(v for v in ids if v[:3] in ("af_", "am_", "bf_", "bm_") and "_v0" not in v)}

    @app.post("/api/voice-setting")
    def voice_setting(body: VoiceBody, request: Request):
        require(request)
        if not VOICE_NAME.match(body.voice):
            raise HTTPException(400, "Unknown voice")
        live_settings.save(config.data_dir, {"tts_voice": body.voice})
        apply_live({"tts_voice": body.voice})
        return {"voice": body.voice}

    # ---- settings (widget and web page): changes apply at once and are kept in settings.json ----
    def settings_view() -> dict:
        return {**live_settings.view(config), "wake_phrase": wake_phrase(config.assistant_name, config.wake_word)}

    @app.get("/api/settings")
    def get_settings(request: Request):
        require(request)
        return settings_view()

    @app.post("/api/settings")
    async def set_settings(request: Request):
        require(request)
        try:
            changes = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON body expected") from None
        if not isinstance(changes, dict):
            raise HTTPException(400, "JSON object expected")
        clean, errors = live_settings.check(changes)
        if errors:
            raise HTTPException(400, {"errors": errors})  # all or nothing: never half-applied
        live_settings.save(config.data_dir, clean)
        apply_live(clean)
        return settings_view()

    @app.post("/api/settings/reload")
    def reload_settings(request: Request):
        """Read settings.json again (e.g. after editing it by hand). Deploy-time values need a restart."""
        require(request)
        fresh = load_config(config.data_dir)
        clean = {k: getattr(fresh, "ollama_url" if k == "llm_url" else k) for k in live_settings.EDITABLE}
        apply_live(clean)
        restart = [k for k in live_settings.READ_ONLY if getattr(fresh, k) != getattr(config, k) and k != "source"]
        return {**settings_view(), "restart_needed": restart}

    @app.post("/api/settings/test-wake")
    def test_wake(body: TextBody, request: Request):
        """Does this phrase (e.g. what Whisper heard) start with the wake word?"""
        require(request)
        rest = strip_wake(body.text, wake_re)
        return {"matches": rest is not None, "rest": rest or ""}

    # ---- projects ----
    @app.get("/api/projects")
    def projects(request: Request):
        require(request)
        return {"projects": project_names()}

    async def send_when_ready(sid: str, task: str) -> None:
        """Wait for the new session's agent to start and show its prompt, then send the task.
        Never types it into a plain shell."""
        # Let at least one poll refresh the screen first: a reused session id can still show the
        # previous session's screen for up to a second.
        await asyncio.sleep(ready_settle + 0.5)
        deadline = time.monotonic() + ready_timeout
        trusted = False
        while time.monotonic() < deadline:
            s = registry.sessions.get(sid)
            if s is not None and TRUST_PROMPT in s.screen_text:
                # First run in a new folder: Claude asks whether to trust it, and the highlighted
                # answer is "No, exit". The user asked for this session, so pick "Yes" once.
                if not trusted:
                    await bridge.send_keys(sid, "\x1b[B")  # Down arrow
                    await asyncio.sleep(0.3)
                    await bridge.send_keys(sid, "\r")
                    trusted = True
                await asyncio.sleep(0.5)
                continue
            if s is not None and (s.hook_seen or agent.is_agent(s.job_name)) and agent.ready_text in s.screen_text:
                await asyncio.sleep(ready_settle)  # let the agent finish drawing its prompt
                s = registry.sessions.get(sid)
                if s is None or TRUST_PROMPT in s.screen_text:
                    continue  # the trust question appeared meanwhile: answer it first
                await bridge.send_text(sid, task)
                registry.mark(sid, Status.WORKING, "", clock())
                registry.record("sent", sid, task, clock())
                return
            await asyncio.sleep(0.2)
        log.warning("new session %s never became ready; task not sent", sid)

    @app.post("/api/new_session")
    async def new_session(body: ProjectBody, request: Request, background: BackgroundTasks):
        require(request)
        if body.path:
            # Any folder on the hub, as long as it is inside its user's home (resolved: no ../ or links out).
            if body.where not in ("", config.hub_server):
                raise HTTPException(400, "A folder path works for sessions on the hub; pick a project for other places")
            home = Path.home().resolve()
            folder = Path(body.path.strip()).expanduser().resolve()
            if folder != home and home not in folder.parents:
                raise HTTPException(400, f"{body.path} is outside your home folder")
            if not folder.is_dir():
                raise HTTPException(404, f"No folder {body.path}")
            sid = await bridge.create_tab(str(folder), agent.launch(config.home))
            registry.reset_hooks(sid)
            if body.task.strip():
                background.add_task(send_when_ready, sid, body.task.strip())
            return {"id": sid}
        if body.where not in ("", "mac", config.hub_server):
            # Another server: the Mac starts it through its tmux gateway there.
            mac = remotes.get("mac")
            if mac is None or not mac.online:
                raise HTTPException(409, "Your Mac is offline.")
            server = next((v for v in mac_servers() if v["name"] == body.where), None)
            if server is None or not server["online"]:
                raise HTTPException(409, f"{body.where} isn't connected.")
            if not valid_name(body.project) or body.project not in server["projects"]:
                raise HTTPException(404, f"Unknown project on {body.where}")
            sid = await act(bridge.create_on_server("mac", body.where, body.project))
            registry.reset_hooks(sid)
            if body.task.strip():
                background.add_task(send_when_ready, sid, body.task.strip())
            return {"id": sid}
        if body.where == "mac":
            mac = remotes.get("mac")
            if mac is None or not mac.online:
                raise HTTPException(409, "Your Mac is offline.")
            if not valid_name(body.project) or body.project not in mac.projects:
                raise HTTPException(404, "Unknown project on your Mac")
            sid = await act(bridge.create_remote("mac", body.project, agent.process))
            registry.reset_hooks(sid)
            if body.task.strip():
                background.add_task(send_when_ready, sid, body.task.strip())
            return {"id": sid}
        if body.create and valid_name(body.project) and body.project not in project_names():
            (config.projects_dir / body.project).mkdir(parents=True, exist_ok=True)  # valid_name: no ../ or /
        if not valid_name(body.project) or body.project not in project_names():
            raise HTTPException(404, "Unknown project")
        dest = config.projects_dir / body.project
        if not dest.exists():
            remote = load_remotes(config.projects_file).get(body.project)
            if remote is None:
                raise HTTPException(404, "Unknown project")
            err = await git_clone(remote, dest)
            if err:
                raise HTTPException(502, f"I couldn't clone {body.project}: {err}")
        sid = await bridge.create_tab(str(config.projects_dir / body.project), agent.launch(config.home))
        registry.reset_hooks(sid)  # a reused id must not inherit the previous session's state
        if body.task.strip():
            background.add_task(send_when_ready, sid, body.task.strip())
        return {"id": sid}

    # ---- voice & commands ----
    @app.post("/api/command")
    def typed_command(body: CommandBody, request: Request):
        require(request)
        return command(body.text, body.selected)

    async def handle_text(text: str, selected: str, wake: str, followup: str, compose: str,
                          confirming: str, final: str, length: str) -> dict:
        """Everything after speech recognition: wake word, model, rules, clean-up. Shared by
        /api/voice (Mac, transcribes first) and /api/utterance (clients that send text)."""
        ignored = {"text": "", "heard": False, "action": None}
        addressed = True
        if wake == "1":
            if not text:
                return ignored
            rest = strip_wake(text, wake_re)
            if rest is None:
                if followup != "1":
                    return ignored  # not addressed to Jarvis: drop without logging
                addressed, rest = False, text
            if final != "1" and rest and (_dangling(rest) or TELL_NO_MESSAGE.match(normalize(rest))):
                # Sounds unfinished ("Jarvis ...", "tell api to ..."): the widget keeps listening
                # and sends the whole thing again, with final=1 if nothing more comes.
                return {"text": "", "heard": True, "action": None, "incomplete": True}
            if not rest:
                return {"text": text, "heard": True, "action": {"type": "speak", "text": "Yes?"}}
            text = rest
        if confirming == "1" and followup == "1":
            # Jarvis asked "Did you mean ...?": yes sends its version, no drops it, anything else is the correction.
            answer = _yes_no(text)
            if answer is not None:
                return {"text": text, "heard": True, "action": {"type": "confirmed" if answer else "cancel"}}
            compose = "1"
        if compose == "1" and followup == "1":
            # Jarvis just asked "What should I send?": this speech is the message itself.
            if parse(text).kind == "cancel":
                return {"text": text, "heard": True, "action": {"type": "cancel"}}
            reply = resolve(Command("reply", text=text.strip()), registry.ordered(), registry.names(),
                            selected or None, command_projects())
            return {"text": text, "heard": True, "action": await polish(reply)}
        if interpreter is not None:
            sessions, names = registry.ordered(), registry.names()
            servers = server_names() if mac_servers() else []
            extra = {"servers": servers} if servers else {}
            data = await run_in_threadpool(lambda: interpreter.interpret(text, sessions, names, selected or None,
                                                                         length, **extra))
            log.info("voice decision: addressed=%s followup=%s model=%s", addressed, followup,
                     data.get("action") if isinstance(data, dict) else "unavailable")
            if data is not None:
                action = llm_action(data, sessions, names, selected or None, command_projects(), servers=servers, agent_name=agent.name,
                                    utterance=text)
                if action is None:
                    return ignored
                return {"text": text, "heard": True, "action": digest(await polish(action))}
        result = command(text, selected)
        if not addressed and result["action"]["type"] in ("reply", "ask"):
            return ignored  # without the model, follow-up speech may only be a known command
        return {**result, "action": await polish(result["action"]), "heard": True}

    @app.post("/api/utterance")
    async def utterance(body: UtteranceBody, request: Request):
        require(request)
        return await handle_text(body.text.strip(), body.selected, body.wake, body.followup, body.compose,
                                 body.confirming, body.final, body.length)

    @app.post("/api/voice")
    async def voice(request: Request, audio: UploadFile = File(...), selected: str = Form(""),
                    wake: str = Form("0"), followup: str = Form("0"), compose: str = Form("0"),
                    confirming: str = Form("0"), final: str = Form("0"), length: str = Form("normal")):
        require(request)
        if transcriber is None:
            raise HTTPException(503, "This server does not do speech recognition; send text to /api/utterance")
        data = await audio.read()
        suffix = Path(audio.filename or "speech.webm").suffix or ".webm"
        names = sorted(set(registry.names().values()))
        # Name the wake word once. A long vocabulary list made Whisper hear "Tag"/"TabDeck";
        # example commands all starting with "Deck," made it invent "Deck" before ordinary speech.
        prompt = f"The assistant is called {whisper_hint}. Tabs: " + ", ".join(names) + "."
        try:
            text = await run_in_threadpool(transcriber.transcribe, data, suffix, prompt)
        except Exception as e:  # noqa: BLE001 - surface any Whisper failure to the client
            raise HTTPException(500, f"Transcription failed: {e}") from e
        return await handle_text(text, selected, wake, followup, compose, confirming, final, length)

    async def polish(action: dict) -> dict:
        """Let the model clean up a dictated reply, or turn it into a question when it is unclear."""
        if interpreter is None or action.get("type") != "reply":
            return action
        s = registry.sessions.get(action["session_id"])
        name = registry.names().get(action["session_id"], "the tab")
        p = await run_in_threadpool(interpreter.polish, action["text"], name, s.message if s else "")
        if p is None:
            return action
        message = p["message"].strip() or action["text"]
        if _dangling(message):
            return {"type": "clarify", "session_id": action["session_id"], "text": message,
                    "speak": f"{message.rstrip('.!?, ')} what? Say the whole message again."}
        if p.get("confident") is False:
            question = str(p.get("question") or "").strip() or f"Did you mean: {message}?"
            return {"type": "clarify", "session_id": action["session_id"], "text": message, "speak": question}
        return {**action, "text": message}

    # ---- Claude Code hook ----
    async def summarize_then_apply(iterm_session: str, event: dict, seq: int) -> None:
        """Mark the tab done only once its spoken summary is ready, so the announcement includes it.
        Dropped if another event (e.g. a new prompt) arrived while summarising."""
        path = event.get("transcript_path")
        sid = iterm_session.split(":")[-1]
        # Prefer the answer Claude Code puts in the event; the transcript file can lag behind it.
        text = str(event.get("last_assistant_message") or "")
        for _ in range(12):
            if text or not path:
                break
            text = await run_in_threadpool(last_assistant_text, path)
            if not text:
                await asyncio.sleep(0.25)
        summary = ""
        # Short replies are already a summary; the model invents filler for near-empty ones.
        if len(clean_for_speech(text)) > SUMMARIZE_OVER:
            name = registry.names().get(sid, "the tab")
            summary = await run_in_threadpool(interpreter.summarize, text, name) or ""
        if not summary and text:
            first = chunk(clean_for_speech(text), 2)
            summary = first[0] if first else ""
        if summary:
            event = {**event, "summary": summary}
        registry.apply_hook(iterm_session, event, clock(), if_seq=seq)

    @app.post("/hook")
    async def hook(request: Request, background: BackgroundTasks):
        require_local(request)
        iterm_session = request.headers.get("x-iterm-session", "")
        pane = request.headers.get("x-tmux-pane", "")
        if not iterm_session and pane.startswith("%") and pane[1:].isdigit():
            iterm_session = "tmux-" + pane[1:]
        if not iterm_session:
            return Response(status_code=204)
        try:
            event = await request.json()
        except ValueError:
            return Response(status_code=400)
        hook_event(iterm_session, event, background.add_task)
        return Response(status_code=204)

    def hook_event(session_id: str, event: dict, schedule) -> None:
        """Apply a Claude hook event. Stop events get their spoken summary first (in the background)."""
        if event.get("hook_event_name") == "Stop" and interpreter is not None:
            schedule(summarize_then_apply, session_id, event, registry.hook_seq(session_id))
        else:
            registry.apply_hook(session_id, event, clock())

    # ---- remote agents (the Mac) ----
    @app.websocket("/agent")
    async def agent_socket(websocket: WebSocket):
        bearer = websocket.headers.get("authorization", "")
        name = agents.verify(bearer[7:]) if agents is not None and bearer.startswith("Bearer ") else None
        remote = remotes.get(name) if name else None
        if remote is None:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            hello = await websocket.receive_json()
        except (WebSocketDisconnect, ValueError):
            return
        send = websocket.send_json  # one bound method: detach() compares it
        remote.attach(send, hello if isinstance(hello, dict) else {})
        log.info("agent %s connected", name)
        try:
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "snapshot":
                    remote.on_snapshot(msg)
                    try:  # show the change now instead of at the next poll
                        registry.update(await bridge.snapshot(), clock())
                    except Exception:  # noqa: BLE001 - a failing local source must not break the socket
                        pass
                elif kind == "result":
                    remote.on_result(msg)
                elif kind == "hook" and str(msg.get("session", "")).startswith(remote.prefix):
                    hook_event(msg["session"], msg.get("event") or {}, lambda fn, *a: asyncio.create_task(fn(*a)))
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            remote.detach(send)
            try:
                registry.update(await bridge.snapshot(), clock())
            except Exception:  # noqa: BLE001
                pass
            log.info("agent %s disconnected", name)

    return app
