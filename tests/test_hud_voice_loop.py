"""Tests for ui/hud/voice_loop.py: VoiceLoopController.

Covers the fix for VoiceListenWorker.error being emitted but never
connected to anything -- a real capture/transcription error (e.g. the
selected microphone producing no signal at all) used to vanish silently,
with the loop just retrying forever and nothing on screen to explain why
voice mode "wasn't working."
"""
from types import SimpleNamespace

from PyQt6.QtTest import QTest

from ui.hud.voice_loop import VoiceLoopController


def _controller(session=None, bridge=None) -> VoiceLoopController:
    return VoiceLoopController(session or SimpleNamespace(), bridge or SimpleNamespace())


def test_on_loop_error_is_forwarded_to_status_message(qapp):
    controller = _controller()
    received = []
    controller.status_message.connect(received.append)

    controller._on_loop_error("no audio detected")

    assert received == ["Voice error: no audio detected"]


def test_start_loop_actually_wires_worker_errors_through(qapp):
    """End-to-end: a real VoiceListenWorker hitting an exception on its
    session must reach status_message, not just the handler in isolation.

    Cleanup note: the worker keeps retrying (raise -> emit -> 1s backoff)
    until stop() is called, and its final state_changed("idle") emit on the
    way out is a queued cross-thread signal that stop_loop()'s blocking
    QThread.wait() does not itself pump the event loop for. Draining with a
    second qWait() after stop_loop() — while `controller` is still alive and
    referenced — matters: a signal left queued past the end of the test can
    get delivered later, against a since-garbage-collected receiver, during
    a completely unrelated test's own processEvents() call (this crashed
    test_hud_window.py's fixture teardown with a real access violation
    before this qWait() was added, not just a Python exception)."""

    def boom(**_kwargs):
        raise RuntimeError("mic trouble")

    session = SimpleNamespace(wake=None, listener=SimpleNamespace(listen=boom))
    controller = _controller(session=session)
    received = []
    controller.status_message.connect(received.append)

    try:
        controller._start_loop()
        QTest.qWait(300)  # let the worker thread hit the exception and emit
        assert any("Voice error: mic trouble" in m for m in received)
    finally:
        controller.stop_loop()
        QTest.qWait(200)  # drain the worker's trailing queued signal(s) before it goes out of scope
