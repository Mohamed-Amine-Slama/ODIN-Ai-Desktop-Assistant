"""Where the HUD's frame time actually goes, counting real repaints."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
import config
from ui.hud import zones
from ui.hud.window import OdinHudWindow
from ui.workers import UiBridge
import hud_data_frames

config.HUD_REDUCED_MOTION = True
app = QApplication([])
brain = MagicMock(); brain.ask.return_value = "..."
session = MagicMock(); session.mode = "text"; session.mic = None
win = OdinHudWindow(brain, session, UiBridge())
win.show(); QTest.qWait(500)
win.orb.set_bands([0.4] * 48)
for frame in hud_data_frames.frames(60):
    win.telemetry_view.render_frame(frame)

# 1. What does the backdrop alone cost to paint once?
backdrop = win._backdrop
pm = QPixmap(backdrop.size())
for _ in range(3): backdrop.render(pm)
t0 = time.perf_counter()
for _ in range(20): backdrop.render(pm)
print(f"_Backdrop.paintEvent (1920x1080)          {(time.perf_counter()-t0)/20*1000:7.2f} ms")

# 2. Count how many widgets actually repaint per animation tick, by
#    instrumenting paintEvent on every descendant.
counts = {}
def instrument(widget):
    original = widget.paintEvent
    name = type(widget).__name__
    def wrapped(event, _o=original, _n=name):
        counts[_n] = counts.get(_n, 0) + 1
        return _o(event)
    widget.paintEvent = wrapped

for w in win.findChildren(type(win.orb).__mro__[-2]):   # every QWidget descendant
    try: instrument(w)
    except Exception: pass
instrument(win._backdrop)

QTest.qWait(50)
counts.clear()
FRAMES = 30
t0 = time.perf_counter()
for _ in range(FRAMES):
    win.telemetry_view.advance_animation()
    app.processEvents()
elapsed = (time.perf_counter() - t0) / FRAMES * 1000
print(f"\nreal frame (advance + Qt repaint):        {elapsed:7.2f} ms  over {FRAMES} frames")
print("\nrepaints per frame, by widget type:")
for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    print(f"   {name:24s} {n/FRAMES:6.2f}")
win.voice.shutdown(); win.dismiss(); win.tray_icon.hide()
