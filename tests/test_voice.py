"""Tests for the voice layer.

The heavy deps (faster-whisper, openwakeword, sounddevice, pygame) are not
installed in CI, so these cover the logic that surrounds them: graceful
degradation, the confirmation parser, and the speech queue.
"""
import sys
from types import SimpleNamespace

import pytest

import config
from core.speech_output import SpeechOutput, _make_engine
from main import NO_WORDS, YES_WORDS, Session


# -- speech output ---------------------------------------------------------

def test_missing_engines_run_silent_not_crash(monkeypatch):
    """No pygame, no pyttsx3, no network: Jarvis must still work in text."""
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    assert _make_engine("off", "x") is None

    speaker = SpeechOutput(engine="off")
    assert speaker.enabled is False
    assert speaker.engine_name == "silent"
    speaker.say("still prints")  # must not raise
    speaker.wait(timeout=1)
    speaker.shutdown()


def test_auto_falls_back_from_edge_to_sapi(monkeypatch):
    """A missing network or pygame must not leave Jarvis mute if SAPI works."""
    built = []

    class FakeSapi:
        name = "sapi"

        def __init__(self, rate):
            built.append(rate)

        def speak(self, text):
            pass

    def boom(voice):
        raise RuntimeError("no network")

    monkeypatch.setattr("core.speech_output.EdgeEngine", boom)
    monkeypatch.setattr("core.speech_output.SapiEngine", FakeSapi)

    engine = _make_engine("auto", "en-GB-RyanNeural")
    assert engine.name == "sapi"
    assert built == [config.TTS_RATE]


def test_say_is_nonblocking_and_wait_drains(monkeypatch):
    spoken = []

    class SlowEngine:
        name = "fake"

        def speak(self, text):
            spoken.append(text)

    monkeypatch.setattr("core.speech_output._make_engine", lambda *a: SlowEngine())
    speaker = SpeechOutput()
    try:
        speaker.say("one.")
        speaker.say("two.")
        speaker.wait(timeout=5)
        assert spoken == ["one.", "two."]
    finally:
        speaker.shutdown()


def test_tts_failure_does_not_kill_the_worker(monkeypatch):
    """A glitch on one sentence must not stop later ones being spoken."""
    spoken = []

    class FlakyEngine:
        name = "flaky"

        def speak(self, text):
            if text == "bad.":
                raise RuntimeError("device gone")
            spoken.append(text)

    monkeypatch.setattr("core.speech_output._make_engine", lambda *a: FlakyEngine())
    speaker = SpeechOutput()
    try:
        speaker.say("bad.")
        speaker.say("good.")
        speaker.wait(timeout=5)
        assert spoken == ["good."]
    finally:
        speaker.shutdown()


# -- spoken confirmation ---------------------------------------------------

class _Listener:
    def __init__(self, answer):
        self.answer = answer

    def listen(self, max_seconds=None):
        return self.answer


def _voice_session(answer, spoken=None):
    speaker = SpeechOutput(engine="off")
    speaker.say = (spoken.append if spoken is not None else (lambda *_: None))
    speaker.wait = lambda *a, **k: None
    session = Session(speaker)
    session.mode = "voice"
    session.listener = _Listener(answer)
    return session


class _Skill:
    def confirmation_prompt(self, **kwargs):
        return "Really shut down the PC?"


@pytest.mark.parametrize("answer", sorted(YES_WORDS))
def test_spoken_yes_confirms(answer):
    assert _voice_session(answer).confirm(_Skill(), {}) is True


@pytest.mark.parametrize("answer", sorted(NO_WORDS))
def test_spoken_no_declines(answer):
    assert _voice_session(answer).confirm(_Skill(), {}) is False


def test_silence_declines():
    """A misheard 'shut down' must never take the machine down because the
    user said nothing back."""
    spoken = []
    assert _voice_session("", spoken).confirm(_Skill(), {}) is False
    assert any("leave it" in s for s in spoken)


def test_unparseable_answer_declines():
    spoken = []
    session = _voice_session("what did you say about the weather", spoken)
    assert session.confirm(_Skill(), {}) is False
    assert any("take that as a no" in s for s in spoken)


def test_trailing_punctuation_is_tolerated():
    """Whisper punctuates: 'Yes.' must still parse as yes."""
    assert _voice_session("Yes.").confirm(_Skill(), {}) is True


# -- degradation -----------------------------------------------------------

def test_voice_mode_reports_failure_instead_of_lying(monkeypatch):
    """The original printed 'Switched to voice mode' and then kept reading
    typed input. It must say what actually happened."""
    monkeypatch.setitem(
        sys.modules,
        "core.audio",
        SimpleNamespace(Microphone=lambda: (_ for _ in ()).throw(RuntimeError("no mic"))),
    )
    session = Session(SpeechOutput(engine="off"))
    message = session.set_mode("voice")

    assert "couldn't start voice mode" in message
    assert session.mode == "text"
    assert session.listener is None


def test_text_mode_needs_no_audio_stack():
    session = Session(SpeechOutput(engine="off"))
    assert session.set_mode("text") == "Switched to text mode."
    assert session.mode == "text"


def test_wake_word_off_returns_no_detector(monkeypatch):
    monkeypatch.setattr(config, "WAKE_WORD", "off", raising=False)
    from core.wake import make_detector

    assert make_detector(mic=None) is None


def test_missing_wake_model_degrades_to_push_to_talk(monkeypatch, capsys):
    """A broken wake word should mean push-to-talk, not a refusal to start."""
    from core.wake import WakeWordUnavailable, make_detector

    monkeypatch.setattr(config, "WAKE_WORD", "hey_jarvis", raising=False)

    def unavailable(mic, *a, **k):
        raise WakeWordUnavailable("openwakeword isn't installed")

    monkeypatch.setattr("core.wake.WakeWordDetector", unavailable)

    assert make_detector(mic=None) is None
    assert "openwakeword isn't installed" in capsys.readouterr().out
