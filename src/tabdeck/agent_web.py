from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .agent import Agent
from .auth import is_local


def create_agent_app(agent: Agent, transcriber, utterance, assistant_hint: str = "Jarvis") -> FastAPI:
    """The Mac agent's localhost-only endpoints: Claude hooks and the widget's voice."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def local_only(request: Request) -> None:
        if not is_local(request.client.host if request.client else ""):
            raise HTTPException(403, "Only from this Mac")

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
                    confirming: str = Form("0"), final: str = Form("0"), length: str = Form("normal")):
        local_only(request)
        if transcriber is None:
            raise HTTPException(503, "No speech recognition on this machine")
        names = sorted({s.project for s in agent.registry.sessions.values()})
        prompt = f"The assistant is called {assistant_hint}. Tabs: " + ", ".join(names) + "."
        text = await run_in_threadpool(transcriber.transcribe, await audio.read(),
                                       Path(audio.filename or "s.webm").suffix or ".webm", prompt)
        return await utterance({"text": text, "selected": selected, "wake": wake, "followup": followup,
                                "compose": compose, "confirming": confirming, "final": final, "length": length})

    return app
