"""Desktop entry point: the orb and the HUD.

Run:  python app.py        (or: python main.py --gui)

The console entry point in main.py is still the fallback — it needs no Qt and
no display, which matters when you are debugging a skill over SSH.
"""
import signal
import sys
import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

import config
from core import gesture, knowledge
from core.brain import Brain, auto_decline, friendly_error
from core.browser import get_browser_controller
from core.discord_channel import DiscordChannel
from core.scheduler import ReminderScheduler, TaskScheduler
from core.speech_output import SpeechOutput
from core.store import get_store
from core.telegram_channel import TelegramChannel
from core.undo import prune_trash
from main import Session
from ui.app_window import OrbWindow
from ui.hud.window import OdinHudWindow
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

    # Built once, off (or a no-op) unless ENABLE_GESTURE_CONTROL is set — see
    # core/gesture.py. The skill (hand_control) and the HUD/tray toggle both
    # reach this same instance via get_gesture_controller().
    gesture_controller = gesture.make_controller(bridge.on_gesture_state)
    gesture.set_gesture_controller(gesture_controller)

    scheduler = ReminderScheduler(store)
    missed = scheduler.fire_due()
    scheduler.start()

    # Recurring scheduled tasks (schedule_task skill): a full brain turn run
    # unattended, on its own thread. Confirmations auto-decline — nobody is
    # there to answer them — but on_text/on_action/on_tool_activity stay the
    # bridge's, so a briefing is spoken and its tool calls show up in the
    # HUD's live trace exactly like a normal turn's would.
    def run_scheduled_task(prompt: str) -> None:
        try:
            brain.ask(prompt, confirm=auto_decline)
        except Exception as e:  # noqa: BLE001 - one bad scheduled run must not kill the poller
            print(f"[scheduled task] {friendly_error(e)}")

    task_scheduler = TaskScheduler(store, run_scheduled_task)
    task_scheduler.start()

    # Telegram/Discord bridges — off unless their bot token is set. Replies go
    # back over that channel as text only (on_text suppressed, so nothing is
    # spoken or pushed into the HUD transcript for a message that didn't
    # originate there), and confirmations auto-decline for the same reason
    # as scheduled tasks: nobody is there to answer them.
    def handle_remote_message(text: str) -> str:
        try:
            return brain.ask(text, confirm=auto_decline, on_text=lambda _s: None)
        except Exception as e:  # noqa: BLE001 - a bad turn must not kill the poll loop
            return friendly_error(e)

    telegram = TelegramChannel(handle_remote_message)
    telegram_live = telegram.start()

    discord = DiscordChannel(handle_remote_message)
    discord_live = discord.start()

    hud = OdinHudWindow(brain, session, bridge)
    orb = OrbWindow()
    orb.summoned.connect(hud.show_and_activate)
    hud.state_changed.connect(orb.set_state)

    hotkey = _Hotkey()
    hotkey.pressed.connect(hud.show_and_activate, Qt.ConnectionType.QueuedConnection)
    bound = hotkey.install(config.HUD_HOTKEY)

    _place_orb(orb)
    orb.show()
    hud.show_and_activate()

    # Load the vector store while the entry animation plays. Without this the
    # very first request pays chromadb's ~2.6s import inline, between the user
    # asking and the prompt leaving — the worst possible moment for it.
    threading.Thread(target=knowledge.warm_up, name="knowledge-warmup", daemon=True).start()

    if restored:
        hud.announce(f"Picked up {restored} messages from your last session.")
    if missed:
        hud.announce(f"Fired {missed} reminder(s) that came due while I was closed.")
    if telegram_live:
        hud.announce("Telegram bridge is live.")
    if discord_live:
        hud.announce("Discord bridge is live.")
    if bound:
        hud.announce(f"Press {config.HUD_HOTKEY} any time to summon me.")

    def shutdown():
        hud.voice.shutdown()
        hud.telemetry.stop()
        hud.telemetry.wait(2000)
        # dismiss() (the window-close path) already stops this; aboutToQuit
        # can also fire without dismiss() running first (tray "Quit"), and
        # without this a mid-request WeatherWorker (up to an 8s timeout)
        # could still be alive when the interpreter starts tearing down.
        hud.weather.stop()
        hud.weather.wait(2000)
        if gesture_controller is not None:
            gesture_controller.stop()
        # Lazily constructed, so this may be a controller that never launched
        # anything — close() on an idle one is a no-op. No startup wiring to
        # match it: unlike the camera, a browser window is its own indicator.
        get_browser_controller().close()
        bridge.release()  # let a worker parked on a confirmation fall through
        scheduler.stop()
        task_scheduler.stop()
        telegram.stop()
        discord.stop()
        session.shutdown()

    app.aboutToQuit.connect(shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
