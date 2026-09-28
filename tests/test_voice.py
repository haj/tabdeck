import io
import wave

import pytest

from tabdeck.voice import Transcriber, clean_transcript


@pytest.mark.parametrize("junk", ["", " ", "Thank you.", "Thanks for watching!", "you", ".", "Bye."])
def test_hallucinations_are_dropped(junk):
    assert clean_transcript(junk) == ""


def test_real_text_is_kept():
    assert clean_transcript("  Go to Atlas. ") == "Go to Atlas."
    assert clean_transcript("Yes.") == "Yes."


@pytest.mark.slow
def test_transcribes_silence_to_nothing():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    assert Transcriber("mlx-community/whisper-large-v3-turbo").transcribe(buf.getvalue(), ".wav") == ""


def seg(text, no_speech=0.1, logprob=-0.3):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def test_text_from_result_drops_silent_segments_and_prompt_echo():
    from tabdeck.voice import text_from_result
    prompt = "Voice command for TabDeck. Tabs: Atlas, Beacon."
    assert text_from_result({"segments": [seg(" Go to Atlas.")]}, prompt) == "Go to Atlas."
    assert text_from_result({"segments": [seg(" Thank you so much.", no_speech=0.9)]}, prompt) == ""
    assert text_from_result({"segments": [seg(" Blah", logprob=-1.5)]}, prompt) == ""
    echo = " Voice command for TabDeck. Tabs: Atlas, Beacon. Extra words"
    assert text_from_result({"segments": [seg(echo)]}, prompt + " Extra words here to lengthen.") == ""
    assert text_from_result({"text": "Okay."}, prompt) == ""


def test_short_command_inside_prompt_is_not_dropped():
    from tabdeck.voice import text_from_result
    prompt = "Deck, status. Deck, next. Deck, go to Atlas. Tabs: Atlas."
    assert text_from_result({"segments": [seg(" Deck.")]}, prompt) == "Deck."
    assert text_from_result({"segments": [seg(" Deck, go to Atlas.")]}, prompt) == "Deck, go to Atlas."


def test_repetitive_hallucination_is_dropped():
    from tabdeck.voice import text_from_result
    loop = " Deck, " + "enter, camp, " * 40
    assert text_from_result({"segments": [{"text": loop, "no_speech_prob": 0.1, "avg_logprob": -0.3,
                                           "compression_ratio": 3.1}]}) == ""
    assert text_from_result({"segments": [{"text": loop, "no_speech_prob": 0.1, "avg_logprob": -0.3}]}) == ""
    ok = {"text": " Deck, tell garden to run the tests.", "no_speech_prob": 0.1, "avg_logprob": -0.3,
          "compression_ratio": 1.1}
    assert text_from_result({"segments": [ok]}) == "Deck, tell garden to run the tests."
