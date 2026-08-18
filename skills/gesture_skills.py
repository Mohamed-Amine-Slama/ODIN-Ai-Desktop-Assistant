"""Voice/text activation for hand-gesture cursor control.

The camera can also be toggled directly from the HUD/tray (ui/hud/window.py)
with no confirmation at all — a deliberate click is already consent. This
skill is the other path: the one a mishearing, or the model itself, can
reach, so 'start' is gated like any other DANGEROUS action. 'stop' never is —
turning the camera off must never be blocked behind a yes/no round-trip.
"""
from core.gesture import get_gesture_controller
from core.risk import Risk

from .base_skill import BaseSkill

GESTURE_VOCABULARY = (
    "Point with your index finger to move the cursor. Pinch thumb and index "
    "to click (hold to drag). Pinch thumb and middle to right-click, thumb "
    "and ring to double-click, thumb and pinky to middle-click. Hold up "
    "index and middle together and move up/down to scroll. Hold up index, "
    "middle, and ring together and move your thumb to zoom. Swipe an open "
    "palm sideways to switch windows. An open palm held still, or a fist, "
    "pauses tracking."
)


class HandControlSkill(BaseSkill):
    name = "hand_control"
    description = (
        "Turn hand-gesture cursor control on or off. When on, a webcam feed "
        "drives the mouse — pointing moves the cursor, pinching clicks or "
        "drags. Starting it activates the camera, so use it only when the "
        "user actually asks for hands-free cursor control."
    )
    input_schema = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["start", "stop"]}},
        "required": ["action"],
    }

    # Only starting is gated. Stopping must always be immediate — the one
    # thing this feature can never do is refuse to turn itself off.
    DANGEROUS_ACTIONS = {"start"}

    def risk_for(self, action: str = "", **_) -> Risk:
        return Risk.DANGEROUS if action in self.DANGEROUS_ACTIONS else Risk.SAFE

    def consequence(self, action: str = "", **_) -> str:
        return f"Turn on hand-gesture camera control? {GESTURE_VOCABULARY}"

    def run(self, action: str) -> str:
        controller = get_gesture_controller()
        if action == "start":
            return controller.start()
        if action == "stop":
            return controller.stop()
        return "action must be 'start' or 'stop'."
