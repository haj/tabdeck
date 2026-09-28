from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import settings as live_settings
from .agent import Agent
from .auth import is_local_request
from .config import Config, load_config

HINT = re.compile(r'^[A-Za-z][A-Za-z .\'"()-]{0,60}$')  # e.g. 'Friday ("Hey Friday")': a name, never instructions
MAC_KEYS = ("mac_tabs", "stt_url", "stt_model")
RESTART_KEYS = ("hub_url", "agent_token", "hub_ca", "port")  # used when the agent starts


def create_agent_app(agent: Agent, transcriber, utterance, assistant_hint: str = "Jarvis",
                     config: Config | None = None) -> FastAPI:
    """The Mac agent's localhost-only endpoints: Claude hooks, the widget's voice, and this Mac's settings."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    config = config or Config()
    base_transcriber = transcriber  # Whisper on this Mac (mlx), when stt_url is empty

    def local_only(request: Request) -> None:
        if not is_local_request(request):  # never through a proxy or tunnel
            raise HTTPException(403, "Only from this Mac")

    def same_origin(request: Request) -> None:
        # The widget sends no Origin; a website open in a browser on this Mac does. Refuse those.
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Not from this page")

    def apply_mac(values: dict) -> None:
        nonlocal transcriber
        if "mac_tabs" in values:
            agent.mac_tabs = values["mac_tabs"]
            agent.registry.version += 1  # send the hub a fresh snapshot
        url = values.get("stt_url", config.stt_url)
        model = values.get("stt_model", config.stt_model)
        if "stt_url" in values or "stt_model" in values:
            if url:
                from .stt import WhisperTranscriber
                transcriber = WhisperTranscriber(url, model)
            else:
                transcriber = base_transcriber

    def mac_view() -> dict:
        return {"values": {"mac_tabs": agent.mac_tabs, "stt_url": config.stt_url, "stt_model": config.stt_model}}

    @app.get("/api/local-settings")
    def get_local(request: Request):
        local_only(request)
        same_origin(request)
        return mac_view()

    @app.post("/api/local-settings")
    async def set_local(request: Request):
        nonlocal config
        local_only(request)
        same_origin(request)
        try:
            changes = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON body expected") from None
        if not isinstance(changes, dict):
            raise HTTPException(400, "JSON object expected")
        clean, errors = {}, []
        for key, value in changes.items():
            if key == "mac_tabs":
                if isinstance(value, bool):
                    clean[key] = value
                else:
                    errors.append("report this Mac's tabs: true or false")
            elif key == "stt_url":
                url, err = live_settings.check_url(str(value or ""), "speech-to-text server", required=False)
                if err:
                    errors.append(err)
                else:
                    clean[key] = url
            elif key == "stt_model":
                if live_settings.MODEL.match(str(value)):
                    clean[key] = value
                else:
                    errors.append(f"model name {value!r}: letters, digits and . _ : / @ -")
            else:
                errors.append(f"unknown setting {key!r} (hub settings are changed on the hub)")
        if errors:
            raise HTTPException(400, {"errors": errors})
        live_settings.save(config.data_dir, clean)
        apply_mac(clean)
        config = live_settings.apply(config, {k: v for k, v in clean.items() if k != "mac_tabs"})
        return mac_view()

    @app.post("/api/local-settings/reload")
    def reload_local(request: Request):
        nonlocal config
        local_only(request)
        same_origin(request)
        fresh = load_config(config.data_dir)
        apply_mac({"mac_tabs": fresh.mac_tabs, "stt_url": fresh.stt_url, "stt_model": fresh.stt_model})
        restart = [k for k in RESTART_KEYS if fresh.settings.get(k) != config.settings.get(k)]
        config = live_settings.apply(config, {"stt_url": fresh.stt_url, "stt_model": fresh.stt_model})
        return {**mac_view(), "restart_needed": restart}

    @app.post("/hook")
    async def hook(request: Request):
        local_only(request)
        iterm_session = request.headers.get("x-iterm-session", "")
        if not iterm_session:
            return Response(status_code=204)
        try:
            event = await request.json()
        except ValueError:
            return Response(status_code=400)
        await agent.hook(iterm_session, event if isinstance(event, dict) else {})
        return Response(status_code=204)

    @app.post("/api/voice")
    async def voice(request: Request, audio: UploadFile = File(...), selected: str = Form(""),
                    wake: str = Form("0"), followup: str = Form("0"), compose: str = Form("0"),
                    confirming: str = Form("0"), final: str = Form("0"), length: str = Form("normal"),
                    hint: str = Form("")):
        local_only(request)
        if transcriber is None:
            raise HTTPException(503, "No speech recognition on this machine")
        names = sorted({s.project for s in agent.registry.sessions.values()})
        # The widget passes the hub's current name ("hint"), so a changed wake word is heard at once.
        called = hint if HINT.match(hint) else assistant_hint
        prompt = f"The assistant is called {called}. Tabs: " + ", ".join(names) + "."
        text = await run_in_threadpool(transcriber.transcribe, await audio.read(),
                                       Path(audio.filename or "s.webm").suffix or ".webm", prompt)
        return await utterance({"text": text, "selected": selected, "wake": wake, "followup": followup,
                                "compose": compose, "confirming": confirming, "final": final, "length": length})

    return app
