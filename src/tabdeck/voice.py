from __future__ import annotations

import io
import logging
import re
import tempfile
import threading
import wave

log = logging.getLogger(__name__)

HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye",
    "thanks", "subtitles by the amaraorg community", "thank you very much",
    "thank you so much", "okay", "ok", "youre welcome",
}
NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0
MAX_COMPRESSION_RATIO = 2.4  # Whisper's own threshold for repetition loops


def _repetitive(text: str) -> bool:
    """True for looping output such as "enter, camp, enter, camp, ..."."""
    words = _norm(text).split()
    if len(words) < 12:
        return False
    return len(set(words)) / len(words) < 0.3


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def clean_transcript(text: str) -> str:
    t = text.strip()
    norm = _norm(t)
    if len(norm) < 2 or norm in HALLUCINATIONS:
        return ""
    return t


def text_from_result(result: dict, prompt: str = "") -> str:
    segments = result.get("segments")
    if segments is None:
        text = result.get("text", "")
    else:
        text = "".join(s.get("text", "") for s in segments
                       if s.get("no_speech_prob", 0) <= NO_SPEECH_PROB
                       and s.get("avg_logprob", 0) >= MIN_AVG_LOGPROB
                       and s.get("compression_ratio", 0) <= MAX_COMPRESSION_RATIO)
    if _repetitive(text):
        return ""
    text = clean_transcript(text)
    # Only long echoes: real commands can legitimately match the prompt's short examples.
    if text and prompt and len(_norm(text).split()) >= 8 and _norm(text) in _norm(prompt):
        return ""  # Whisper echoing the vocabulary prompt on silence
    return text


class Transcriber:
    def __init__(self, model: str):
        self.model = model
        self._lock = threading.Lock()

    def transcribe(self, audio: bytes, suffix: str, prompt: str = "") -> str:
        import mlx_whisper

        with tempfile.NamedTemporaryFile(suffix=suffix) as f:
            f.write(audio)
            f.flush()
            with self._lock:
                result = mlx_whisper.transcribe(
                    f.name, path_or_hf_repo=self.model, language="en",
                    initial_prompt=prompt or None, condition_on_previous_text=False)
        return text_from_result(result, prompt)

    def warm(self) -> None:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 8000)
        try:
            self.transcribe(buf.getvalue(), ".wav")
            log.info("whisper model loaded: %s", self.model)
        except Exception:  # noqa: BLE001
            log.exception("whisper warm-up failed")
