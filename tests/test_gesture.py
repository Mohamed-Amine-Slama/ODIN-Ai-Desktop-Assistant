"""Tests for core/gesture.py: hand-gesture cursor control.

classify_pose, GestureStateMachine, and map_to_screen are pure — no camera,
mediapipe, or pyautogui needed for those. GestureController's lifecycle is
tested with cv2/mediapipe/pyautogui faked via sys.modules, the same technique
tests/test_window_input_skills.py already uses for pyautogui, so this runs
without any of those packages actually installed.
"""
import sys
import time
from types import ModuleType

import pytest

import config
from core.gesture import (
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_PIP,
    MIDDLE_TIP,
    PINKY_PIP,
    PINKY_TIP,
    RING_PIP,
    RING_TIP,
    THUMB_TIP,
    Action,
    FRAME_MARGIN,
    GestureController,
    GestureStateMachine,
    Landmark,
    Pose,
    classify_pose,
    get_gesture_controller,
    make_controller,
    map_to_screen,
    set_gesture_controller,
)

# -- pose fixtures -----------------------------------------------------------
# Baseline: a curled fist with the thumb held well away from every fingertip,
# so no pinch is ever accidentally in range. Tests override just the joints
# that matter for the pose under test.

CURLED_TIP, CURLED_PIP = 0.6, 0.5    # tip.y > pip.y -> not extended
EXTENDED_TIP, EXTENDED_PIP = 0.1, 0.5  # tip.y < pip.y -> extended
FAR_THUMB = (0.95, 0.05)             # nowhere near any fingertip below


def _hand(overrides: dict | None = None) -> list[Landmark]:
    """overrides maps a landmark index (an int, so it can't be passed as a
    **kwarg) to an (x, y) tuple."""
    lm = [Landmark(0.5, 0.5) for _ in range(21)]
    lm[THUMB_TIP] = Landmark(*FAR_THUMB)
    for idx in (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP):
        lm[idx] = Landmark(0.5, CURLED_TIP)
    for idx in (INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP):
        lm[idx] = Landmark(0.5, CURLED_PIP)
    for idx, (x, y) in (overrides or {}).items():
        lm[idx] = Landmark(x, y)
    return lm


class TestClassifyPose:
    def test_no_hand_is_none(self):
        assert classify_pose(None) == Pose.NONE

    def test_too_few_landmarks_is_none(self):
        assert classify_pose([Landmark(0, 0)] * 5) == Pose.NONE

    def test_fist_is_fist(self):
        assert classify_pose(_hand()) == Pose.FIST

    def test_index_only_is_point(self):
        hand = _hand({INDEX_TIP: (0.5, EXTENDED_TIP), INDEX_PIP: (0.5, EXTENDED_PIP)})
        assert classify_pose(hand) == Pose.POINT

    def test_index_and_middle_is_two_finger(self):
        hand = _hand({
            INDEX_TIP: (0.5, EXTENDED_TIP), INDEX_PIP: (0.5, EXTENDED_PIP),
            MIDDLE_TIP: (0.5, EXTENDED_TIP), MIDDLE_PIP: (0.5, EXTENDED_PIP),
        })
        assert classify_pose(hand) == Pose.TWO_FINGER

    def test_all_extended_is_palm(self):
        hand = _hand({
            INDEX_TIP: (0.5, EXTENDED_TIP), INDEX_PIP: (0.5, EXTENDED_PIP),
            MIDDLE_TIP: (0.5, EXTENDED_TIP), MIDDLE_PIP: (0.5, EXTENDED_PIP),
            RING_TIP: (0.5, EXTENDED_TIP), RING_PIP: (0.5, EXTENDED_PIP),
            PINKY_TIP: (0.5, EXTENDED_TIP), PINKY_PIP: (0.5, EXTENDED_PIP),
        })
        assert classify_pose(hand) == Pose.PALM

    def test_thumb_near_index_is_pinch_index(self):
        hand = _hand({THUMB_TIP: (0.5, 0.61), INDEX_TIP: (0.5, 0.6)})
        assert classify_pose(hand) == Pose.PINCH_INDEX

    def test_thumb_near_middle_is_pinch_middle(self):
        # index_tip pushed out of the default curled position too, so it
        # doesn't also land within pinch range of the thumb by coincidence.
        hand = _hand({THUMB_TIP: (0.5, 0.61), MIDDLE_TIP: (0.5, 0.6), INDEX_TIP: (0.9, 0.9)})
        assert classify_pose(hand) == Pose.PINCH_MIDDLE

    def test_three_fingers_up_is_none(self):
        """Not a recognised pose -> NONE (neutral), never misread as a
        different gesture."""
        hand = _hand({
            INDEX_TIP: (0.5, EXTENDED_TIP), INDEX_PIP: (0.5, EXTENDED_PIP),
            MIDDLE_TIP: (0.5, EXTENDED_TIP), MIDDLE_PIP: (0.5, EXTENDED_PIP),
            RING_TIP: (0.5, EXTENDED_TIP), RING_PIP: (0.5, EXTENDED_PIP),
        })
        assert classify_pose(hand) == Pose.NONE


# -- state machine ------------------------------------------------------------

class TestGestureStateMachine:
    def test_point_moves(self):
        m = GestureStateMachine(smoothing=1.0)  # no smoothing lag for assertions
        events = m.update(Pose.POINT, 0.3, 0.4, now=0.0)
        assert len(events) == 1
        assert events[0].action == Action.MOVE
        assert events[0].x == pytest.approx(0.3)
        assert events[0].y == pytest.approx(0.4)

    def test_quick_pinch_is_a_click_on_release(self):
        m = GestureStateMachine(click_hold_seconds=0.3, smoothing=1.0)
        assert m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.0) == []
        # Released well before the hold threshold.
        events = m.update(Pose.POINT, 0.5, 0.5, now=0.05)
        assert [e.action for e in events] == [Action.CLICK, Action.MOVE]
        assert m.dragging is False

    def test_held_pinch_becomes_a_drag(self):
        m = GestureStateMachine(click_hold_seconds=0.2, smoothing=1.0)
        assert m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.0) == []
        events = m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.25)
        assert [e.action for e in events] == [Action.DRAG_START]
        assert m.dragging is True
        events = m.update(Pose.PINCH_INDEX, 0.6, 0.6, now=0.30)
        assert [e.action for e in events] == [Action.DRAG_MOVE]

    def test_releasing_a_drag_ends_it_without_a_click(self):
        m = GestureStateMachine(click_hold_seconds=0.2, smoothing=1.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.25)  # -> drag
        events = m.update(Pose.POINT, 0.5, 0.5, now=0.5)
        assert [e.action for e in events] == [Action.DRAG_END, Action.MOVE]

    def test_losing_the_hand_mid_drag_releases_it(self):
        """A drag left dangling with a physically stuck mouse button would be
        a real hazard, not just a bug."""
        m = GestureStateMachine(click_hold_seconds=0.2, smoothing=1.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.25)  # -> drag
        events = m.update(Pose.NONE, 0.0, 0.0, now=0.5)
        assert [e.action for e in events] == [Action.DRAG_END]
        assert m.dragging is False

    def test_palm_mid_drag_releases_it(self):
        m = GestureStateMachine(click_hold_seconds=0.2, smoothing=1.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.0)
        m.update(Pose.PINCH_INDEX, 0.5, 0.5, now=0.25)  # -> drag
        events = m.update(Pose.PALM, 0.5, 0.5, now=0.5)
        assert [e.action for e in events] == [Action.DRAG_END]

    def test_pinch_middle_right_clicks_once_while_held(self):
        m = GestureStateMachine(smoothing=1.0)
        first = m.update(Pose.PINCH_MIDDLE, 0.5, 0.5, now=0.0)
        again = m.update(Pose.PINCH_MIDDLE, 0.5, 0.5, now=0.1)
        released = m.update(Pose.POINT, 0.5, 0.5, now=0.2)
        assert [e.action for e in first] == [Action.RIGHT_CLICK]
        assert again == []
        assert [e.action for e in released] == [Action.MOVE]

    def test_two_finger_vertical_move_scrolls(self):
        m = GestureStateMachine(smoothing=1.0)
        m.update(Pose.TWO_FINGER, 0.5, 0.5, now=0.0)
        events = m.update(Pose.TWO_FINGER, 0.5, 0.3, now=0.1)  # moved up
        assert [e.action for e in events] == [Action.SCROLL]
        assert events[0].scroll_amount > 0  # moving up scrolls up (positive)

    def test_tiny_two_finger_jitter_does_not_scroll(self):
        m = GestureStateMachine(smoothing=1.0)
        m.update(Pose.TWO_FINGER, 0.5, 0.500, now=0.0)
        events = m.update(Pose.TWO_FINGER, 0.5, 0.501, now=0.1)
        assert events == []

    def test_neutral_pose_never_moves_the_cursor(self):
        m = GestureStateMachine(smoothing=1.0)
        assert m.update(Pose.FIST, 0.5, 0.5, now=0.0) == []
        assert m.update(Pose.NONE, 0.5, 0.5, now=0.1) == []


# -- coordinate mapping --------------------------------------------------------

class TestMapToScreen:
    BOUNDS = (0, 0, 1920, 1080)

    def test_center_maps_to_center_regardless_of_mirroring(self):
        assert map_to_screen(0.5, 0.5, self.BOUNDS, mirror_x=True) == (960, 540)
        assert map_to_screen(0.5, 0.5, self.BOUNDS, mirror_x=False) == (960, 540)

    def test_inner_margin_edges_reach_screen_edges(self):
        assert map_to_screen(FRAME_MARGIN, 0.5, self.BOUNDS, mirror_x=False) == (0, 540)
        assert map_to_screen(1 - FRAME_MARGIN, 0.5, self.BOUNDS, mirror_x=False)[0] == 1920

    def test_beyond_margin_clamps_rather_than_going_offscreen(self):
        x, _ = map_to_screen(0.0, 0.5, self.BOUNDS, mirror_x=False)
        assert x == 0
        x, _ = map_to_screen(1.0, 0.5, self.BOUNDS, mirror_x=False)
        assert x == 1920

    def test_mirroring_flips_left_and_right(self):
        left, _ = map_to_screen(0.2, 0.5, self.BOUNDS, mirror_x=False)
        mirrored, _ = map_to_screen(0.2, 0.5, self.BOUNDS, mirror_x=True)
        # Independently rounded floats, not exact mirrors bit-for-bit.
        assert mirrored == pytest.approx(1920 - left, abs=1)

    def test_offset_origin_bounds_are_respected(self):
        """A monitor placed left of the primary sits at negative coordinates
        -- the mapping must land inside that box too, not just at (0, 0)."""
        bounds = (-1920, 0, 0, 1080)
        assert map_to_screen(0.5, 0.5, bounds, mirror_x=False) == (-960, 540)


# -- GestureController lifecycle ----------------------------------------------

class _FakeCapture:
    def __init__(self, opens=True):
        self._opens = opens
        self.released = False

    def isOpened(self):
        return self._opens

    def read(self):
        return True, "frame"

    def release(self):
        self.released = True


class _FakeLandmarker:
    """Stands in for a mediapipe Tasks-API HandLandmarker. No hand is ever
    detected, so the loop just idles."""

    def __init__(self):
        self.closed = False

    def detect(self, _image):
        from types import SimpleNamespace
        return SimpleNamespace(hand_landmarks=[])

    def close(self):
        self.closed = True


def _install_fake_camera_stack(monkeypatch, opens=True, landmarker_error=None):
    """Fakes cv2, mediapipe, and pyautogui so GestureController._run() can
    execute for real without any of those packages installed, mirroring
    test_window_input_skills.py's pyautogui fixture.

    mediapipe's Tasks API (mediapipe.tasks.python...) is a deep submodule
    chain that isn't worth faking at the sys.modules boundary; instead
    core.gesture._hand_landmarker (its one call site) is patched directly.
    mp.Image/mp.ImageFormat are still real top-level attributes _run() reads
    directly, so those are faked on the mediapipe module itself.
    """
    from types import SimpleNamespace, ModuleType

    fake_cv2 = ModuleType("cv2")
    fake_cv2.VideoCapture = lambda index: _FakeCapture(opens=opens)
    fake_cv2.cvtColor = lambda frame, code: frame
    fake_cv2.COLOR_BGR2RGB = 4
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    fake_mediapipe = ModuleType("mediapipe")
    fake_mediapipe.Image = lambda image_format, data: data
    fake_mediapipe.ImageFormat = SimpleNamespace(SRGB=1)
    monkeypatch.setitem(sys.modules, "mediapipe", fake_mediapipe)

    if landmarker_error is None:
        monkeypatch.setattr("core.gesture._hand_landmarker", lambda: (_FakeLandmarker(), None))
    else:
        monkeypatch.setattr("core.gesture._hand_landmarker", lambda: (None, landmarker_error))

    class _FailSafeException(Exception):
        pass

    fake_pyautogui = ModuleType("pyautogui")
    fake_pyautogui.FAILSAFE = True
    fake_pyautogui.FailSafeException = _FailSafeException
    fake_pyautogui.size = lambda: (1920, 1080)
    fake_pyautogui.moveTo = lambda x, y: None
    fake_pyautogui.mouseDown = lambda: None
    fake_pyautogui.mouseUp = lambda: None
    fake_pyautogui.click = lambda button="left": None
    fake_pyautogui.scroll = lambda amount: None
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    monkeypatch.setattr("core.gesture.IS_WINDOWS", False)


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)


class TestGestureController:
    def test_stop_before_start_does_not_raise(self):
        controller = GestureController()
        controller.stop()  # must not raise

    def test_start_is_a_noop_while_already_running(self, monkeypatch):
        _install_fake_camera_stack(monkeypatch)
        controller = GestureController()
        controller.start()
        try:
            first_thread = controller._thread
            controller.start()
            assert controller._thread is first_thread
        finally:
            controller.stop()

    def test_start_reaches_active_and_stop_reaches_idle(self, monkeypatch):
        _install_fake_camera_stack(monkeypatch)
        states = []
        controller = GestureController(on_state_change=lambda s, m: states.append(s))
        controller.start()
        try:
            _wait_until(lambda: "active" in states)
            assert "active" in states
        finally:
            controller.stop()
        assert states[-1] == "idle"

    def test_camera_that_wont_open_reports_an_error(self, monkeypatch):
        _install_fake_camera_stack(monkeypatch, opens=False)
        states = []
        controller = GestureController(on_state_change=lambda s, m: states.append((s, m)))
        controller.start()
        _wait_until(lambda: states)
        assert states[0][0] == "error"
        assert not controller.is_running()

    def test_missing_opencv_reports_a_friendly_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cv2", None)  # forces ImportError
        states = []
        controller = GestureController(on_state_change=lambda s, m: states.append((s, m)))
        controller.start()
        _wait_until(lambda: states)
        assert states[0][0] == "error"
        assert "opencv-python" in states[0][1]

    def test_landmarker_build_failure_reports_an_error(self, monkeypatch):
        """E.g. the one-time model download failed (core.gesture._ensure_model)
        — must degrade to a message, not crash the capture thread."""
        _install_fake_camera_stack(monkeypatch, landmarker_error="Couldn't download the hand-tracking model: timed out")
        states = []
        controller = GestureController(on_state_change=lambda s, m: states.append((s, m)))
        controller.start()
        _wait_until(lambda: states)
        assert states[0] == ("error", "Couldn't download the hand-tracking model: timed out")
        assert not controller.is_running()


# -- model download -------------------------------------------------------------

class TestEnsureModel:
    def test_uses_a_cached_file_without_downloading(self, monkeypatch, tmp_path):
        from core.gesture import _ensure_model

        cached = tmp_path / "hand_landmarker.task"
        cached.write_bytes(b"already here")
        monkeypatch.setattr("core.gesture._model_path", lambda: str(cached))

        def boom(*a, **k):
            raise AssertionError("should not re-download a cached model")

        monkeypatch.setitem(sys.modules, "requests", _fake_requests_module(boom))

        path, err = _ensure_model()
        assert path == str(cached)
        assert err is None

    def test_downloads_and_caches_when_missing(self, monkeypatch, tmp_path):
        from core.gesture import _ensure_model

        target = tmp_path / "models" / "hand_landmarker.task"
        monkeypatch.setattr("core.gesture._model_path", lambda: str(target))

        class _FakeResponse:
            content = b"model bytes"

            def raise_for_status(self):
                pass

        fake_requests = _fake_requests_module(lambda url, timeout: _FakeResponse())
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        path, err = _ensure_model()
        assert err is None
        assert path == str(target)
        assert target.read_bytes() == b"model bytes"
        assert not target.with_suffix(".task.part").exists()

    def test_download_failure_is_a_message_not_a_crash(self, monkeypatch, tmp_path):
        from core.gesture import _ensure_model

        monkeypatch.setattr("core.gesture._model_path", lambda: str(tmp_path / "hand_landmarker.task"))

        def boom(url, timeout):
            raise OSError("network down")

        monkeypatch.setitem(sys.modules, "requests", _fake_requests_module(boom))

        path, err = _ensure_model()
        assert path is None
        assert "network down" in err


def _fake_requests_module(get_fn):
    module = ModuleType("requests")
    module.get = get_fn
    return module


# -- make_controller / singleton -----------------------------------------------

class TestControllerWiring:
    def test_make_controller_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_GESTURE_CONTROL", False)
        assert make_controller() is None

    def test_make_controller_builds_one_when_enabled(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_GESTURE_CONTROL", True)
        controller = make_controller()
        assert isinstance(controller, GestureController)

    def test_get_and_set_gesture_controller_round_trip(self):
        sentinel = GestureController()
        set_gesture_controller(sentinel)
        try:
            assert get_gesture_controller() is sentinel
        finally:
            set_gesture_controller(None)

    def test_get_gesture_controller_never_returns_none(self):
        set_gesture_controller(None)
        assert isinstance(get_gesture_controller(), GestureController)
        set_gesture_controller(None)
