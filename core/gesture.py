"""Hand-gesture cursor control — camera in, cursor out.

Two layers, deliberately kept apart so the interesting logic is testable
without a camera, mediapipe, or pyautogui anywhere nearby:

- `classify_pose` + `GestureStateMachine` are pure. They turn one frame's 21
  hand landmarks into a named pose, and a stream of poses into high-level
  cursor/keyboard actions (move, click variants, drag, scroll, zoom, window
  switch). No I/O, no OS calls.
- `GestureController` is the impure shell: it owns the camera, the mediapipe
  model, and the actual pyautogui calls, on a background thread — the same
  start()/stop() shape as core/barge_in.py's BargeInWatcher.

Frames are processed in memory and discarded. Nothing here writes a frame to
disk or sends one anywhere — unlike skills/vision_skills.py's see_screen, this
never reaches the model.

Hand detection uses mediapipe's Tasks API (HandLandmarker), not the older
`mp.solutions.hands` — that legacy surface has been dropped from the
mediapipe package this project pins. The Tasks API needs a small (~8MB)
model file that isn't bundled with the package; it's downloaded once to
data/models/ on first use, the same one-time-download shape faster-whisper
and sentence-transformers already use elsewhere in this project.
"""
import os
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum

import config

IS_WINDOWS = sys.platform == "win32"

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# MediaPipe Hands landmark indices used here (of 21 total per hand).
THUMB_TIP = 4
INDEX_PIP = 6
INDEX_TIP = 8
MIDDLE_PIP = 10
MIDDLE_TIP = 12
RING_PIP = 14
RING_TIP = 16
PINKY_PIP = 18
PINKY_TIP = 20

# Normalized (0..1, image-diagonal-relative) thumb-to-fingertip distance
# below which two fingertips count as pinched together. Tuned against a
# typical hand-fills-a-third-of-the-frame webcam shot; revisit if pinches
# fire too eagerly or not at all at your camera's typical distance.
PINCH_THRESHOLD = 0.06

# How far in from each camera-frame edge the "active" tracking region starts.
# Without this, reaching a screen corner means pushing your hand to the
# camera's physical edge, where landmarks are least reliable.
FRAME_MARGIN = 0.15

# Minimum thumb-to-index span change (same normalized units as PINCH_THRESHOLD)
# between frames before a pinch-to-zoom pose registers a step, so sensor
# jitter alone never fires a zoom event.
ZOOM_DEADZONE = 0.01
# Scales a span delta into a Ctrl+scroll amount; matches the scroll gesture's
# own y-delta * 40 so the two continuous gestures feel equally responsive.
ZOOM_SENSITIVITY = 40

# Normalized horizontal palm travel (raw camera frame, pre-mirroring) needed
# to register as a deliberate swipe rather than an idle hand held in frame.
SWIPE_THRESHOLD = 0.15


@dataclass
class Landmark:
    x: float
    y: float
    z: float = 0.0


class Pose(Enum):
    NONE = "none"           # no hand, or not confidently any pose below
    POINT = "point"          # index only — move
    TWO_FINGER = "two_finger"  # index + middle — scroll
    THREE_FINGER = "three_finger"  # index + middle + ring — pinch-to-zoom
    PINCH_INDEX = "pinch_index"    # thumb+index together — click / drag
    PINCH_MIDDLE = "pinch_middle"  # thumb+middle together — right-click
    PINCH_RING = "pinch_ring"      # thumb+ring together — double-click
    PINCH_PINKY = "pinch_pinky"    # thumb+pinky together — middle-click
    PALM = "palm"            # all five extended — neutral, or a swipe
    FIST = "fist"            # all five curled — neutral


class Action(Enum):
    MOVE = "move"
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    MIDDLE_CLICK = "middle_click"
    DRAG_START = "drag_start"
    DRAG_MOVE = "drag_move"
    DRAG_END = "drag_end"
    SCROLL = "scroll"
    ZOOM = "zoom"
    ALT_TAB = "alt_tab"
    ALT_SHIFT_TAB = "alt_shift_tab"


@dataclass
class GestureEvent:
    action: Action
    x: float | None = None          # normalized 0..1, smoothed
    y: float | None = None
    scroll_amount: int | None = None


def _dist(a: Landmark, b: Landmark) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _extended(tip: Landmark, pip: Landmark) -> bool:
    """A non-thumb finger counts as extended when its tip sits above (smaller
    y, image space) its own PIP joint — tolerant of camera angle and doesn't
    need handedness or the wrist at all."""
    return tip.y < pip.y


def classify_pose(landmarks: "list[Landmark] | None") -> Pose:
    """One frame's 21 hand landmarks -> a single named pose. Pure and
    stateless — frame-to-frame memory (tap vs. hold, drag lifecycle) lives in
    GestureStateMachine below."""
    if not landmarks or len(landmarks) < 21:
        return Pose.NONE

    thumb_tip = landmarks[THUMB_TIP]
    index_tip = landmarks[INDEX_TIP]
    middle_tip = landmarks[MIDDLE_TIP]
    ring_tip = landmarks[RING_TIP]
    pinky_tip = landmarks[PINKY_TIP]

    if _dist(thumb_tip, index_tip) < PINCH_THRESHOLD:
        return Pose.PINCH_INDEX
    if _dist(thumb_tip, middle_tip) < PINCH_THRESHOLD:
        return Pose.PINCH_MIDDLE
    if _dist(thumb_tip, ring_tip) < PINCH_THRESHOLD:
        return Pose.PINCH_RING
    if _dist(thumb_tip, pinky_tip) < PINCH_THRESHOLD:
        return Pose.PINCH_PINKY

    index_up = _extended(index_tip, landmarks[INDEX_PIP])
    middle_up = _extended(middle_tip, landmarks[MIDDLE_PIP])
    ring_up = _extended(ring_tip, landmarks[RING_PIP])
    pinky_up = _extended(pinky_tip, landmarks[PINKY_PIP])

    if index_up and middle_up and ring_up and pinky_up:
        return Pose.PALM
    if not index_up and not middle_up and not ring_up and not pinky_up:
        return Pose.FIST
    if index_up and middle_up and ring_up and not pinky_up:
        return Pose.THREE_FINGER
    if index_up and middle_up and not ring_up and not pinky_up:
        return Pose.TWO_FINGER
    if index_up and not middle_up and not ring_up and not pinky_up:
        return Pose.POINT
    return Pose.NONE


class GestureStateMachine:
    """Turns a stream of (pose, x_norm, y_norm, timestamp) samples into
    high-level cursor actions. Pure logic, no camera/OS calls.

    A pinch shorter than click_hold_seconds is a click; held longer, it
    becomes a drag. Losing the hand, or making a fist (the one purely
    "neutral" pose), always ends any in-progress drag rather than leaving a
    mouse button stuck down. An open palm also ends a drag, but unlike a fist
    it isn't purely neutral — holding it still is neutral, sweeping it
    sideways fires a window-switch (Alt+Tab) instead.
    """

    def __init__(self, click_hold_seconds: float | None = None, smoothing: float | None = None):
        self.click_hold_seconds = (
            click_hold_seconds if click_hold_seconds is not None
            else config.GESTURE_CLICK_HOLD_MS / 1000.0
        )
        self.smoothing = smoothing if smoothing is not None else config.GESTURE_SMOOTHING
        self._smoothed_x: float | None = None
        self._smoothed_y: float | None = None
        self._pinch_started_at: float | None = None
        self._dragging = False
        self._right_clicked = False
        self._ring_clicked = False
        self._pinky_clicked = False
        self._last_two_finger_y: float | None = None
        self._zoom_start_span: float | None = None
        self._palm_start_x: float | None = None

    @property
    def dragging(self) -> bool:
        return self._dragging

    def _smooth(self, x_norm: float, y_norm: float) -> tuple[float, float]:
        if self._smoothed_x is None:
            self._smoothed_x, self._smoothed_y = x_norm, y_norm
        else:
            a = self.smoothing
            self._smoothed_x = a * x_norm + (1 - a) * self._smoothed_x
            self._smoothed_y = a * y_norm + (1 - a) * self._smoothed_y
        return self._smoothed_x, self._smoothed_y

    def _end_index_pinch(self, events: list) -> None:
        """A pinch that never became a drag was a click. Called whenever the
        pose changes away from PINCH_INDEX, or the hand is lost/neutral."""
        if self._dragging:
            events.append(GestureEvent(Action.DRAG_END))
            self._dragging = False
        elif self._pinch_started_at is not None:
            events.append(GestureEvent(Action.CLICK))
        self._pinch_started_at = None

    def reset(self) -> None:
        """Drop all tracked state — hand lost or gone neutral, so a new hand
        entering frame doesn't inherit a stale drag or pinch timer."""
        self._smoothed_x = self._smoothed_y = None
        self._pinch_started_at = None
        self._dragging = False
        self._right_clicked = False
        self._ring_clicked = False
        self._pinky_clicked = False
        self._last_two_finger_y = None
        self._zoom_start_span = None
        self._palm_start_x = None

    def _reset_other_trackers(self, events: list, keep: str) -> None:
        """Every non-neutral pose owns exactly one continuous tracker or
        single-fire flag below; clear the others each frame so switching
        gestures never inherits stale state from whichever gesture was
        active a moment ago (e.g. a leftover scroll anchor making the first
        frame of a fresh two-finger gesture jump)."""
        if keep != "pinch_index":
            self._end_index_pinch(events)
        if keep != "two_finger":
            self._last_two_finger_y = None
        if keep != "three_finger":
            self._zoom_start_span = None
        if keep != "palm":
            self._palm_start_x = None
        if keep != "pinch_middle":
            self._right_clicked = False
        if keep != "pinch_ring":
            self._ring_clicked = False
        if keep != "pinch_pinky":
            self._pinky_clicked = False

    def update(
        self, pose: Pose, x_norm: float, y_norm: float, spread: float = 0.0, now: "float | None" = None
    ) -> "list[GestureEvent]":
        """`spread` is the current normalized thumb-to-index-tip distance —
        only meaningful for THREE_FINGER (pinch-to-zoom), which needs a
        continuous analog signal that the discrete `pose` alone can't carry.
        Every other pose ignores it."""
        now = time.monotonic() if now is None else now
        events: list[GestureEvent] = []

        if pose in (Pose.NONE, Pose.FIST):
            self._end_index_pinch(events)
            self.reset()
            return events

        sx, sy = self._smooth(x_norm, y_norm)

        if pose == Pose.POINT:
            self._reset_other_trackers(events, keep="point")
            events.append(GestureEvent(Action.MOVE, x=sx, y=sy))

        elif pose == Pose.TWO_FINGER:
            self._reset_other_trackers(events, keep="two_finger")
            if self._last_two_finger_y is not None:
                delta = self._last_two_finger_y - sy
                if abs(delta) > 0.01:
                    events.append(GestureEvent(Action.SCROLL, scroll_amount=int(delta * 40)))
            self._last_two_finger_y = sy

        elif pose == Pose.THREE_FINGER:
            self._reset_other_trackers(events, keep="three_finger")
            if self._zoom_start_span is None:
                self._zoom_start_span = spread
            else:
                delta = spread - self._zoom_start_span
                if abs(delta) > ZOOM_DEADZONE:
                    events.append(GestureEvent(Action.ZOOM, scroll_amount=int(delta * ZOOM_SENSITIVITY)))
                    self._zoom_start_span = spread

        elif pose == Pose.PALM:
            # Neutral when held still (matches FIST/NONE's old behavior via
            # _reset_other_trackers ending any drag); a deliberate horizontal
            # swipe fires a window switch instead of just idling.
            self._reset_other_trackers(events, keep="palm")
            if self._palm_start_x is None:
                self._palm_start_x = x_norm
            else:
                # Raw camera x, not mirrored: map_to_screen() flips x so a
                # physical rightward hand move feels like moving the cursor
                # right; the same physical swipe therefore *decreases*
                # x_norm here, so the sign below matches that convention.
                delta = self._palm_start_x - x_norm
                if delta > SWIPE_THRESHOLD:
                    events.append(GestureEvent(Action.ALT_TAB))
                    self._palm_start_x = x_norm
                elif delta < -SWIPE_THRESHOLD:
                    events.append(GestureEvent(Action.ALT_SHIFT_TAB))
                    self._palm_start_x = x_norm

        elif pose == Pose.PINCH_INDEX:
            self._reset_other_trackers(events, keep="pinch_index")
            if self._pinch_started_at is None:
                self._pinch_started_at = now
            held = now - self._pinch_started_at
            if self._dragging:
                events.append(GestureEvent(Action.DRAG_MOVE, x=sx, y=sy))
            elif held >= self.click_hold_seconds:
                self._dragging = True
                events.append(GestureEvent(Action.DRAG_START, x=sx, y=sy))

        elif pose == Pose.PINCH_MIDDLE:
            self._reset_other_trackers(events, keep="pinch_middle")
            if not self._right_clicked:
                events.append(GestureEvent(Action.RIGHT_CLICK))
                self._right_clicked = True

        elif pose == Pose.PINCH_RING:
            self._reset_other_trackers(events, keep="pinch_ring")
            if not self._ring_clicked:
                events.append(GestureEvent(Action.DOUBLE_CLICK))
                self._ring_clicked = True

        elif pose == Pose.PINCH_PINKY:
            self._reset_other_trackers(events, keep="pinch_pinky")
            if not self._pinky_clicked:
                events.append(GestureEvent(Action.MIDDLE_CLICK))
                self._pinky_clicked = True

        return events


def _virtual_screen_bounds() -> "tuple[int, int, int, int]":
    """(left, top, right, bottom) of the full virtual desktop.

    Duplicated from skills/input_skills.py rather than imported from it:
    skills/ imports core/, never the reverse (see graphify-out's zero
    import-cycle report), and this ~15-line GetSystemMetrics lookup is cheap
    enough to keep both sides of that boundary self-contained.
    """
    if IS_WINDOWS:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            if width > 0 and height > 0:
                left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
                top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
                return left, top, left + width, top + height
        except Exception:
            pass
    import pyautogui
    width, height = pyautogui.size()
    return 0, 0, width, height


def _rescale(v: float) -> float:
    v = (v - FRAME_MARGIN) / (1.0 - 2 * FRAME_MARGIN)
    return min(1.0, max(0.0, v))


def map_to_screen(
    x_norm: float, y_norm: float, bounds: "tuple[int, int, int, int]", mirror_x: bool = True
) -> "tuple[int, int]":
    """Map a normalized (0..1) camera-frame point to a real screen pixel
    inside `bounds`. Rescales the frame's inner region (FRAME_MARGIN in from
    each edge) to the full screen and clamps the rest, so reaching a screen
    corner doesn't require pushing a hand to the camera's physical edge.

    mirror_x defaults on: facing the camera, moving a hand to *your* right
    should move the cursor right, which is the mirror image of the raw frame.
    """
    if mirror_x:
        x_norm = 1.0 - x_norm
    left, top, right, bottom = bounds
    screen_x = left + _rescale(x_norm) * (right - left)
    screen_y = top + _rescale(y_norm) * (bottom - top)
    return int(screen_x), int(screen_y)


def _cv2():
    try:
        import cv2
    except ImportError:
        return None, "Hand control needs the 'opencv-python' package. Run: pip install opencv-python"
    except Exception as e:
        return None, f"Camera capture is unavailable: {e}"
    return cv2, None


def _mediapipe():
    try:
        import mediapipe as mp
    except ImportError:
        return None, "Hand control needs the 'mediapipe' package. Run: pip install mediapipe"
    except Exception as e:
        return None, f"Hand tracking is unavailable: {e}"
    return mp, None


# Google's official hosted hand-landmark model bundle for the Tasks API — see
# https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker. Not
# bundled with the mediapipe package itself, so it's fetched once and cached.
HAND_LANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def _model_path() -> str:
    return os.path.join(config.DATA_DIR, "models", "hand_landmarker.task")


def _ensure_model():
    """Return (path, error_message) for the cached hand-landmark model,
    downloading it on first use. Never raises — a network hiccup here
    degrades to a friendly message, same as a missing package."""
    path = _model_path()
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, None
    try:
        import requests

        os.makedirs(os.path.dirname(path), exist_ok=True)
        response = requests.get(HAND_LANDMARKER_MODEL_URL, timeout=30)
        response.raise_for_status()
        # Download to a temp file and rename atomically, so a connection
        # dropped mid-download can never leave a corrupt file that
        # os.path.getsize(path) > 0 would wrongly treat as cached.
        tmp_path = path + ".part"
        with open(tmp_path, "wb") as f:
            f.write(response.content)
        os.replace(tmp_path, path)
        return path, None
    except Exception as e:
        return None, f"Couldn't download the hand-tracking model: {e}"


def _hand_landmarker():
    """Build a Tasks-API hand landmark detector. Returns (landmarker,
    error_message); the caller still needs _mediapipe() separately for
    mp.Image/mp.ImageFormat, which don't depend on this model."""
    try:
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions
    except ImportError:
        return None, "Hand control needs the 'mediapipe' package. Run: pip install mediapipe"
    except Exception as e:
        return None, f"Hand tracking is unavailable: {e}"

    model_path, err = _ensure_model()
    if err:
        return None, err

    try:
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        return HandLandmarker.create_from_options(options), None
    except Exception as e:
        return None, f"Hand tracking is unavailable: {e}"


def _pyautogui():
    try:
        import pyautogui
    except ImportError:
        return None, "Hand control needs the 'pyautogui' package. Run: pip install pyautogui"
    except Exception as e:
        return None, f"Input control is unavailable: {e}"
    pyautogui.FAILSAFE = True
    return pyautogui, None


class GestureController:
    """Camera-driven cursor control. start()/stop() manage a background
    thread, mirroring core/barge_in.py's BargeInWatcher — cheap to call
    repeatedly; start() is a no-op if already running.

    on_state_change(state, message) fires from the capture thread, not the
    caller's: state is "active" once tracking begins, or "error" (with a
    human-readable reason) if the loop can't continue. Reporting "idle" after
    a clean stop is stop()'s job, not the loop's, so the two paths can't race
    to report contradictory states.
    """

    def __init__(self, on_state_change=None, camera_index: "int | None" = None):
        self.on_state_change = on_state_change or (lambda state, message: None)
        self.camera_index = camera_index if camera_index is not None else config.GESTURE_CAMERA_INDEX
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        if self.is_running():
            return "Hand control is already on."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return "Hand control is starting."

    def stop(self) -> str:
        if not self.is_running():
            self._thread = None
            return "Hand control is already off."
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        self.on_state_change("idle", "Hand control is off.")
        return "Hand control is off."

    def _run(self) -> None:
        cv2, err = _cv2()
        if err:
            self.on_state_change("error", err)
            return
        mp, err = _mediapipe()
        if err:
            self.on_state_change("error", err)
            return
        landmarker, err = _hand_landmarker()
        if err:
            self.on_state_change("error", err)
            return
        gui, err = _pyautogui()
        if err:
            self.on_state_change("error", err)
            return

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            cap.release()
            self.on_state_change("error", f"Couldn't open camera {self.camera_index}.")
            return

        machine = GestureStateMachine()
        bounds = _virtual_screen_bounds()
        min_frame_seconds = 1.0 / max(1, config.GESTURE_FPS_LIMIT)
        self.on_state_change("active", "Hand control is on.")

        try:
            while not self._stop.is_set():
                frame_start = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    self._stop.wait(min_frame_seconds)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(image)
                landmarks = None
                if result.hand_landmarks:
                    landmarks = [Landmark(p.x, p.y, p.z) for p in result.hand_landmarks[0]]

                pose = classify_pose(landmarks)
                x_norm = landmarks[INDEX_TIP].x if landmarks else 0.0
                y_norm = landmarks[INDEX_TIP].y if landmarks else 0.0
                spread = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) if landmarks else 0.0

                for event in machine.update(pose, x_norm, y_norm, spread):
                    self._apply(gui, event, bounds)

                elapsed = time.monotonic() - frame_start
                if elapsed < min_frame_seconds:
                    self._stop.wait(min_frame_seconds - elapsed)
        except gui.FailSafeException:
            self.on_state_change(
                "error", "Hand control was stopped by the mouse failsafe (cursor hit a screen corner)."
            )
        except Exception as e:  # noqa: BLE001 - one bad frame must not leak a stuck mouse button
            self.on_state_change("error", f"Hand control stopped: {e}")
        finally:
            if machine.dragging:
                try:
                    gui.mouseUp()
                except Exception:
                    pass
            landmarker.close()
            cap.release()

    def _apply(self, gui, event: GestureEvent, bounds) -> None:
        if event.action in (Action.MOVE, Action.DRAG_MOVE):
            x, y = map_to_screen(event.x, event.y, bounds)
            gui.moveTo(x, y)
        elif event.action == Action.DRAG_START:
            x, y = map_to_screen(event.x, event.y, bounds)
            gui.moveTo(x, y)
            gui.mouseDown()
        elif event.action == Action.DRAG_END:
            gui.mouseUp()
        elif event.action == Action.CLICK:
            gui.click()
        elif event.action == Action.RIGHT_CLICK:
            gui.click(button="right")
        elif event.action == Action.DOUBLE_CLICK:
            gui.doubleClick()
        elif event.action == Action.MIDDLE_CLICK:
            gui.click(button="middle")
        elif event.action == Action.SCROLL:
            gui.scroll(event.scroll_amount or 0)
        elif event.action == Action.ZOOM:
            # try/finally: one bad frame must not leak a stuck Ctrl key, the
            # same reasoning core/gesture.py already applies to mouse buttons.
            gui.keyDown("ctrl")
            try:
                gui.scroll(event.scroll_amount or 0)
            finally:
                gui.keyUp("ctrl")
        elif event.action == Action.ALT_TAB:
            gui.hotkey("alt", "tab")
        elif event.action == Action.ALT_SHIFT_TAB:
            gui.hotkey("alt", "shift", "tab")


def make_controller(on_state_change=None) -> "GestureController | None":
    """Build the hand-control controller, or None if the feature is off.

    Never raises: a broken build here must not stop ODIN from starting.
    Camera and package availability are checked lazily inside start()/_run(),
    on the capture thread — this only checks the kill switch.
    """
    if not getattr(config, "ENABLE_GESTURE_CONTROL", False):
        return None
    try:
        return GestureController(on_state_change=on_state_change)
    except Exception as e:
        print(f"[gesture] {e}")
        return None


_CONTROLLER: "GestureController | None" = None
_CONTROLLER_LOCK = threading.Lock()


def get_gesture_controller() -> GestureController:
    """Process-wide singleton, mirroring core.undo.get_journal(). Falls back
    to a bare, flag-blind GestureController if nothing has called
    set_gesture_controller() yet, so a skill invoked before app startup wiring
    (e.g. in a test) still gets a real, usable object rather than None."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = GestureController()
        return _CONTROLLER


def set_gesture_controller(controller: "GestureController | None") -> None:
    """Replace the process-wide controller. Used by app.py at startup (so the
    skill and the tray toggle both drive the one real instance, wired with
    the UI's on_state_change) and by tests."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        _CONTROLLER = controller
