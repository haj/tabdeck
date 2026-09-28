from __future__ import annotations

import logging

import httpx

from .voice import clean_transcript

log = logging.getLogger(__name__)


def _post(url: str, files: dict, data: dict, timeout: float) -> dict:
    r = httpx.post(url, files=files, data=data, timeout=timeout)
    r.raise_for_status()
    return r.json()


class OdsTranscriber:
    """Speech-to-text through ODS's Whisper service (speaches, OpenAI-compatible /v1/audio/transcriptions),
    so no Whisper runs on the Mac."""

    def __init__(self, url: str, model: str = "Systran/faster-whisper-base", language: str = "en",
                 timeout: float = 60.0, post=_post):
        self.endpoint = url.rstrip("/") + "/v1/audio/transcriptions"
        self.model, self.language, self.timeout, self._post = model, language, timeout, post

    def transcribe(self, audio: bytes, suffix: str, prompt: str = "") -> str:
        files = {"file": (f"speech{suffix}", audio, "application/octet-stream")}
        data = {"model": self.model, "prompt": prompt, "language": self.language}
        try:
            text = str(self._post(self.endpoint, files, data, self.timeout).get("text") or "")
        except Exception as e:  # noqa: BLE001 - ODS down: nothing heard
            log.warning("ODS speech-to-text unavailable: %s", e)
            return ""
        return clean_transcript(text)

    def warm(self) -> None:
        pass  # the ODS service keeps its model loaded
