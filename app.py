"""Desktop entry point: the orb and the HUD.

Run:  python app.py        (or: python main.py --gui)

The console entry point in main.py is still the fallback — it needs no Qt and
no display, which matters when you are debugging a skill over SSH.
"""
import signal
import sys

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

import config
from core.brain import Brain
from core.scheduler import ReminderScheduler
from core.speech_output import SpeechOutput
from core.store import get_store
from core.undo import prune_trash
from main import Session
from ui.app_window import JarvisMainWindow, OrbWindow
from ui.workers import UiBridge


class _Hotkey(QObject):
    """Global hotkey support, if the optional `keyboard` package is installed.

    Its callbacks arrive on the library's own thread, so they are turned into a
    signal rather than touching a widget directly.
    """

    pressed = pyqtSignal()

    def install(self, combo: str) -> bool:
        if not combo or combo.lower() == "off":
            return False
        try:
            import keyboard
        except Exception:
            return False
        try:
            keyboard.add_hotkey(combo, self.pressed.emit)
        except Exception as e:  # no permission, unknown combo, no input device
            print(f"[hotkey] couldn't bind {combo}: {e}")
            return False
        return True


def _place_orb(orb: OrbWindow) -> None:
    """Bottom-right of the primary screen, clear of the taskbar."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    orb.move(area.right() - orb.width() - 32, area.bottom() - orb.height() - 32)


def main() -> None:
    config.ensure_dirs()
    prune_trash()

    # Ctrl-C in the launching console should still kill the process.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName(config.ASSISTANT_NAME)
    # Hiding the HUD must not exit: Jarvis is meant to stay resident behind the
    # orb. Quit comes from the tray menu or /quit.
    app.setQuitOnLastWindowClosed(False)

    # Without a key the OpenAI client refuses to construct at all, so there is
    # no HUD to put the message in. Say so in a dialog rather than dying with a
    # traceback into a console the user may not even have open.
    problem = config.missing_key_message()
    if problem:
        print(problem)
        QMessageBox.critical(None, f"{config.ASSISTANT_NAME} can't start", problem)
        return

    speaker = SpeechOutput()
    session = Session(speaker)
    store = get_store()

    # One bridge, wired once. The worker thread never reassigns these.
    bridge = UiBridge(speaker=speaker)
    brain = Brain(
        confirm=bridge.confirm,
        on_text=bridge.on_text,
        store=store,
        on_action=bridge.on_action,
        on_tool_activity=bridge.on_tool_activity,
    )
    restored = brain.load_history()

    scheduler = ReminderScheduler(store)
    missed = scheduler.fire_due()
    scheduler.start()

    hud = JarvisMainWindow(brain, session, bridge=bridge)
    orb = OrbWindow()
    orb.summoned.connect(hud.show_and_activate)
    hud.state_changed.connect(orb.set_state)

    hotkey = _Hotkey()
    hotkey.pressed.connect(hud.show_and_activate, Qt.ConnectionType.QueuedConnection)
    bound = hotkey.install(config.HUD_HOTKEY)

    _place_orb(orb)
    orb.show()
    hud.show_and_activate()

    if restored:
        hud.append_jarvis_message(f"Picked up {restored} messages from your last session.")
    if missed:
        hud.append_jarvis_message(f"Fired {missed} reminder(s) that came due while I was closed.")
    if bound:
        hud.append_jarvis_message(f"Press {config.HUD_HOTKEY} any time to summon me.")

    def shutdown():
        bridge.release()  # let a worker parked on a confirmation fall through
        scheduler.stop()
        session.shutdown()

    app.aboutToQuit.connect(shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
