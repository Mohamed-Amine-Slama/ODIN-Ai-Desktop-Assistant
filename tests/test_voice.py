"""Tests for the voice layer.

The heavy deps (faster-whisper, openwakeword, sounddevice, pygame) are not
installed in CI, so these cover the logic that surrounds them: graceful
degradation, the confirmation parser, and the speech queue.
"""
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import config
from core.speech_output import EdgeEngine, SapiEngine, SpeechOutput, _make_engine
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


def test_stop_drains_the_queue_and_stops_the_engine(monkeypatch):
    stopped = []
    spoken = []

    class ControllableEngine:
        name = "fake"

        def speak(self, text):
            spoken.append(text)

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr("core.speech_output._make_engine", lambda *a: ControllableEngine())
    speaker = SpeechOutput()
    try:
        speaker.say("one.")
        speaker.say("two.")
        speaker.say("three.")
        speaker.stop()
        assert stopped == [True]
        speaker.wait(timeout=1)  # must not hang on drained backlog
    finally:
        speaker.shutdown()


def test_edge_engine_stop_actually_stops_the_mixer(monkeypatch):
    """The real EdgeEngine had no stop() method at all — SpeechOutput.stop()
    calling self._engine.stop() against it was always an AttributeError,
    swallowed by SpeechOutput.stop()'s own bare except, so barge-in never
    actually interrupted audio already playing through pygame.mixer.music.
    Every other test here uses a fake engine that *does* implement stop(),
    which is exactly what hid this."""
    calls = []

    class FakeMusic:
        def stop(self):
            calls.append("stop")

    class FakeMixer:
        music = FakeMusic()

        def init(self):
            pass

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pygame", SimpleNamespace(mixer=FakeMixer()))

    engine = EdgeEngine("en-GB-RyanNeural")
    engine.stop()

    assert calls == ["stop"]


def test_sapi_engine_stop_actually_stops_the_underlying_engine(monkeypatch):
    """Same missing-stop() defect as EdgeEngine, for the offline pyttsx3
    fallback."""
    calls = []

    class FakePyttsx3Engine:
        def setProperty(self, name, value):  # noqa: ARG002
            pass

        def stop(self):
            calls.append("stop")

    monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(init=lambda: FakePyttsx3Engine()))

    engine = SapiEngine(config.TTS_RATE)
    engine.stop()

    assert calls == ["stop"]


def test_edge_engine_synthesize_failure_does_not_leak_the_temp_file(monkeypatch):
    """tempfile.mkstemp() runs before the network call — if that call then
    fails, the file it already created must not be left behind."""
    import core.speech_output as speech_output_module

    class FakeCommunicate:
        def __init__(self, text, voice):  # noqa: ARG002
            pass

        async def save(self, path):  # noqa: ARG002
            raise RuntimeError("network down")

    class FakeMixer:
        music = SimpleNamespace(stop=lambda: None)

        def init(self):
            pass

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate))
    monkeypatch.setitem(sys.modules, "pygame", SimpleNamespace(mixer=FakeMixer()))

    engine = EdgeEngine("en-GB-RyanNeural")

    created_paths = []
    real_mkstemp = speech_output_module.tempfile.mkstemp

    def spy_mkstemp(*a, **k):
        fd, path = real_mkstemp(*a, **k)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(speech_output_module.tempfile, "mkstemp", spy_mkstemp)

    with pytest.raises(RuntimeError):
        engine._synthesize("hello")

    assert created_paths
    assert not os.path.exists(created_paths[0])


def test_vad_threshold_does_not_deadlock_on_the_first_block():
    """Seeding the noise floor from the first block's own level made
    threshold = level * 3 on that very first call — "level >= level * 3" is
    false for any level > 0, so real speech starting with no leading
    silence would deterministically miss its own first block, and
    steady-volume continuous speech barely nudges the floor away from that
    self-referential trap on the blocks right after (can stall the whole
    recording out to VAD_MAX_SECONDS instead of just missing one block)."""
    from core.speech_input import SpeechInput

    instance = SpeechInput.__new__(SpeechInput)  # __init__ would load a real Whisper model
    instance._noise_floor = None

    level = 0.03  # within config.py's documented ~0.02-0.05 typical-speech range
    threshold = instance._threshold(level, heard_speech=False)

    assert level >= threshold


def test_record_unsubscribes_even_if_something_raises_mid_loop(monkeypatch):
    """A bare (non-try/finally) unsubscribe after the loop skips cleanup on
    any exception other than the one already caught around q.get() —
    permanently leaking this queue as a registered consumer, so every
    future audio block gets appended to a queue nothing will ever drain
    again."""
    import queue as queue_module

    import numpy as np

    from core.speech_input import SpeechInput

    class FakeMic:
        def __init__(self):
            self.block_size = 1280
            self.sample_rate = 16000
            self.unsubscribed = []
            self._q = queue_module.Queue()
            self._q.put(np.zeros(1280, dtype=np.int16))

        def subscribe(self):
            return self._q

        def unsubscribe(self, q):
            self.unsubscribed.append(q)

    mic = FakeMic()
    instance = SpeechInput.__new__(SpeechInput)
    instance.mic = mic
    instance._np = np
    instance._noise_floor = None

    def _boom(block, np):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr("core.speech_input.rms", _boom)

    with pytest.raises(RuntimeError):
        instance._record(max_seconds=1.0)

    assert mic.unsubscribed == [mic._q]


def test_is_speaking_reflects_playback_state(monkeypatch):
    gate = threading.Event()

    class SlowEngine:
        name = "fake"

        def speak(self, text):  # noqa: ARG002
            gate.wait(timeout=2)

        def stop(self):
            gate.set()

    monkeypatch.setattr("core.speech_output._make_engine", lambda *a: SlowEngine())
    speaker = SpeechOutput()
    try:
        assert speaker.is_speaking() is False
        speaker.say("hang on.")
        deadline = time.time() + 2
        while not speaker.is_speaking() and time.time() < deadline:
            time.sleep(0.01)
        assert speaker.is_speaking() is True
        speaker.stop()
        speaker.wait(timeout=2)
        assert speaker.is_speaking() is False
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
    def consequence(self, **kwargs):
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


# -- barge-in ---------------------------------------------------------------

def test_speak_starts_the_barge_in_watcher_and_still_speaks():
    started = []
    spoken = []

    class _Watcher:
        def start(self):
            started.append(True)

    session = Session(SpeechOutput(engine="off"))
    session.speaker.say = spoken.append
    session.barge_in = _Watcher()

    session.speak("hello.")

    assert started == [True]
    assert spoken == ["hello."]


def test_speak_without_a_watcher_still_speaks():
    """Text mode / push-to-talk without barge-in wired up must not break."""
    spoken = []
    session = Session(SpeechOutput(engine="off"))
    session.speaker.say = spoken.append

    session.speak("hello.")

    assert spoken == ["hello."]


def test_on_barge_in_stops_speech_and_captures_the_interruption():
    stopped = []
    session = Session(SpeechOutput(engine="off"))
    session.speaker = SimpleNamespace(stop=lambda: stopped.append(True))
    session.listener = _Listener("wait, stop")

    session._on_barge_in()

    assert stopped == [True]
    assert session._pending_utterance == "wait, stop"


def test_on_barge_in_with_nothing_heard_leaves_no_pending_utterance():
    session = Session(SpeechOutput(engine="off"))
    session.speaker = SimpleNamespace(stop=lambda: None)
    session.listener = _Listener("")

    session._on_barge_in()

    assert session._pending_utterance is None


def test_read_input_drains_a_pending_utterance_first():
    """A captured interruption must be handled before waiting on the wake
    word again, and only once."""
    session = Session(SpeechOutput(engine="off"))
    session._pending_utterance = "interrupting utterance"

    assert session.read_input() == "interrupting utterance"
    assert session._pending_utterance is None


def test_missing_wake_model_degrades_to_push_to_talk(monkeypatch, capsys):
    """A broken wake word should mean push-to-talk, not a refusal to start."""
    from core.wake import WakeWordUnavailable, make_detector

    monkeypatch.setattr(config, "WAKE_WORD", "hey_jarvis", raising=False)

    def unavailable(mic, *a, **k):
        raise WakeWordUnavailable("openwakeword isn't installed")

    monkeypatch.setattr("core.wake.WakeWordDetector", unavailable)

    assert make_detector(mic=None) is None
    assert "openwakeword isn't installed" in capsys.readouterr().out
