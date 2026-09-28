from tabdeck.stt import WhisperTranscriber


def test_transcribes_through_ods_whisper_and_drops_hallucinations():
    calls = []

    def post(url, files, data, timeout):
        calls.append((url, files["file"][0], data))
        return {"text": " Approve it. "}
    t = WhisperTranscriber("http://ods:9100/", model="Systran/faster-whisper-base", post=post)
    assert t.transcribe(b"RIFF...", ".wav", prompt="Atlas") == "Approve it."
    url, filename, data = calls[0]
    assert url == "http://ods:9100/v1/audio/transcriptions" and filename == "speech.wav"
    assert data == {"model": "Systran/faster-whisper-base", "prompt": "Atlas", "language": "en"}
    t2 = WhisperTranscriber("http://ods:9100", post=lambda *a, **k: {"text": "Thank you."})
    assert t2.transcribe(b"x", ".webm") == ""  # Whisper's silence hallucination


def test_failure_gives_empty_text():
    def boom(*a, **k):
        raise OSError("down")
    assert WhisperTranscriber("http://ods:9100", post=boom).transcribe(b"x", ".wav") == ""
