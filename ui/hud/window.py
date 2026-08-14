"""OdinHudWindow — ODIN-HUD.md §4's full-screen instrument HUD, assembled
from the zones defined in ui/hud/layout.py. Replaces the old chat-bubble
JarvisMainWindow as ODIN's one HUD; the small always-on-top ambient
OrbWindow (ui/app_window.py) is unrelated and untouched.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)

import config
from core import learning_status
from core.store import get_store
from core.undo import get_journal
from ui.hud import tokens
from ui.hud.boot import run_boot_sequence
from ui.hud.confirm import ConfirmationBannerWidget
from ui.hud.telemetry import TelemetryFrame, TelemetryWorker
from ui.hud.telemetry_view import TelemetryPresenter
from ui.hud.voice_loop import VoiceLoopController
from ui.hud.weather import WeatherSample, WeatherWorker
from ui.hud.widgets import BarMeter
from ui.hud.zones import ZoneBuilderMixin
from ui.workers import BrainWorker, SkillLogEntry, UiBridge

ANIMATION_FPS = 30

# Orb ring launchers (§6.7) — a different 8-label set from the dock.
LAUNCHER_PRESETS = {
    "SYS": "open task manager",
    "FILES": "open file explorer",
    "WEB": "open my default browser",
    "CODE": "open vs code",
    "MUSIC": "open spotify",
    "VOL": "open the volume control",
    "LEARN": None,  # opens the knowledge dialog locally
    "PWR": "open the Windows power options",
}


class OdinHudWindow(ZoneBuilderMixin, QMainWindow):
    """The full-screen instrument HUD."""

    state_changed = pyqtSignal(str)  # mirrors the old JarvisMainWindow signal, for OrbWindow

    def __init__(self, brain, session, bridge: UiBridge, parent=None):
        super().__init__(parent)
        self.brain = brain
        self.session = session
        self.bridge = bridge

        self.current_worker: BrainWorker | None = None
        self.voice = VoiceLoopController(session, bridge, parent=self)
        self._skill_log: deque[SkillLogEntry] = deque(maxlen=6)
        self._confirm_banner: ConfirmationBannerWidget | None = None
        self._odin_reply_parts: list[str] = []
        self._knowledge_rows: list[QWidget] = []
        self._active_learning: tuple[str, str, float] | None = None
        self._boot_anims = None
        self._shown_once = False

        self.setWindowTitle(f"{config.ASSISTANT_NAME} HUD")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(1920, 1080)
        # NoFocus is QWidget's default — without this, self.setFocus() in
        # show_and_activate() would silently do nothing and keyboard focus
        # would stay wherever Qt's initial auto-focus put it.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.telemetry = TelemetryWorker(self)
        self.telemetry.frame_ready.connect(self._on_frame)

        self.weather = WeatherWorker(self)
        self.weather.weather_ready.connect(self._on_weather)

        self._notes_timer = QTimer(self)
        self._notes_timer.timeout.connect(self._refresh_notes_panel)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_animation_tick)

        # _build_zone_f (ui/hud/zones.py) calls self._on_clock_tick()
        # synchronously during _build_ui() to paint the clock once before
        # its 1s timer's first tick — telemetry_view must exist first.
        self.telemetry_view = TelemetryPresenter(self)

        self._build_ui()
        self._init_tray()
        self._wire_bridge()
        self._wire_voice()
        self._refresh_knowledge_panel()
        self._refresh_notes_panel()

        learning_status.set_callback(self.bridge.report_learning_progress)

    # -- construction --------------------------------------------------

    def _init_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(tokens.CY_300, 3))
        painter.drawEllipse(QRectF(4, 4, 24, 24))
        painter.setBrush(tokens.CY_100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(12, 12, 8, 8))
        painter.end()
        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip(f"{config.ASSISTANT_NAME} — AI desktop assistant")

        menu = QMenu(self)
        for text, slot in (
            (f"Show {config.ASSISTANT_NAME}", self.show_and_activate),
            ("Toggle voice / text mode", self.voice.toggle_mode),
            ("Clear conversation", self.trigger_reset),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _wire_bridge(self) -> None:
        self.bridge.text_chunk.connect(self._on_text_chunk)
        self.bridge.action_reported.connect(self._on_action_reported)
        self.bridge.confirm_requested.connect(self._on_confirm_requested)
        self.bridge.tool_started.connect(self._on_tool_started)
        self.bridge.tool_finished.connect(self._on_tool_finished)
        self.bridge.skill_logged.connect(self._on_skill_logged)
        self.bridge.mic_rms.connect(self.orb.set_mic_level)
        self.bridge.learning_progress.connect(self._on_learning_progress)
        self.bridge.kb_changed.connect(self._on_kb_changed)

    def _wire_voice(self) -> None:
        self.voice.heard.connect(self._on_voice_heard)
        self.voice.state_changed.connect(self._on_voice_state)
        self.voice.status_message.connect(self.console.echo)
        self.voice.greeting_ready.connect(self._on_voice_greeting)

    def _on_voice_greeting(self, text: str) -> None:
        self.transcript_odin.setText(text)
        self.console.echo(text)

    # -- window lifecycle --------------------------------------------------

    def _size_to_screen(self) -> None:
        """Match the real primary screen instead of the 1920x1080 fallback
        set at construction. setFixedSize() and showFullScreen() otherwise
        fight each other on any monitor that isn't exactly 1920x1080 — Qt
        can't actually resize a fixed-size window to fill the real screen,
        which left background layers (sized from the stale 1920x1080) out
        of sync with the window's real geometry. Safe to call repeatedly;
        it's a no-op once the size already matches."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        size = screen.geometry().size()
        if size == self.size():
            return
        self.setFixedSize(size)
        self._backdrop.setGeometry(0, 0, size.width(), size.height())
        self._circuit_traces.setGeometry(0, 0, size.width(), size.height())
        self.console.move((size.width() - self.console.width()) // 2, (size.height() - self.console.height()) // 2)

    def show_and_activate(self) -> None:
        self._size_to_screen()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        # Qt hands keyboard focus to the first tab-focusable child (the
        # EXP dock button) the moment the window is shown, unless something
        # else claims it explicitly. Left that way, every keystroke —
        # including Esc — goes to that button instead of the window, which
        # is why Esc silently did nothing. Claiming focus on the window
        # itself here (not a specific child) is what makes Esc reach
        # keyPressEvent below.
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Belt-and-suspenders: on Windows, activateWindow() on a frameless
        # always-on-top window launched from a terminal can lose a race with
        # the OS's foreground-lock — the window paints but doesn't actually
        # take OS-level keyboard focus until a beat later. A second attempt
        # after the event loop has spun costs nothing and closes that gap.
        QTimer.singleShot(150, lambda: (self.activateWindow(), self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)))
        if not self.telemetry.isRunning():
            self.telemetry.start()
        if not self._anim_timer.isActive():
            self.telemetry_view.reset_animation_clock()
            self._anim_timer.start(int(1000 / ANIMATION_FPS))
        if not self.weather.isRunning():
            self.weather.start()
        if not self._notes_timer.isActive():
            self._notes_timer.start(20_000)
        self.spectrum.start_capture()
        if not self._shown_once:
            self._shown_once = True
            self.transcript_odin.setText(f"{config.ASSISTANT_NAME} is waking up…")
            run_boot_sequence(self)
            self.voice.start_on_boot()

    def dismiss(self) -> None:
        self.telemetry.stop()
        self.telemetry.wait(2000)
        self.weather.stop()
        self.weather.wait(2000)
        self._notes_timer.stop()
        self._anim_timer.stop()
        self.spectrum.stop_capture()
        self.voice.stop_mic_meter()
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.console.isVisible():
                self.console.hide_console()
            else:
                self.dismiss()
            return
        super().keyPressEvent(event)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.dismiss() if self.isVisible() else self.show_and_activate()

    def closeEvent(self, event) -> None:
        if self.tray_icon is not None and self.tray_icon.isVisible():
            self.dismiss()
            event.ignore()
        else:
            event.accept()

    # -- shared ~30fps animation loop (ODIN-HUD.md §10) ---------------------

    def _on_animation_tick(self) -> None:
        self.telemetry_view.advance_animation()

    # -- telemetry -----------------------------------------------------

    def _on_frame(self, frame: TelemetryFrame) -> None:
        self.telemetry_view.render_frame(frame)

    def _on_clock_tick(self) -> None:
        self.telemetry_view.render_clock()

    # -- weather ---------------------------------------------------------

    def _on_weather(self, sample: WeatherSample | None) -> None:
        self.telemetry_view.render_weather(sample)

    # -- notes / reminders -------------------------------------------------

    def _refresh_notes_panel(self) -> None:
        self.telemetry_view.refresh_notes_panel()

    # -- orb / bridge event handling ----------------------------------------

    def _on_orb_status(self, text: str) -> None:
        self.orb_status_label.setText(text)
        color = tokens.CRIT if self.orb.state == "error" else tokens.CY_300
        self.orb_status_label.setStyleSheet(f"color: {color.name()};")

    def set_status(self, state: str) -> None:
        self.orb.state = "thinking" if state == "confirm" else state
        self.state_changed.emit(self.orb.state)

    def _on_text_chunk(self, sentence: str) -> None:
        self.set_status("speaking")
        self._odin_reply_parts.append(sentence)
        self.transcript_odin.setText(" ".join(self._odin_reply_parts))

    def _on_action_reported(self, skill_name: str, token: str, description: str) -> None:  # noqa: ARG002
        pass  # superseded by the skill-log panel; undo is still reachable via /undo

    def _on_tool_started(self, skill_name: str, _args_brief: str) -> None:  # noqa: ARG002
        if skill_name == "deep_learn":
            self.orb.state = "learning"

    def _on_tool_finished(self, skill_name: str, is_error: bool, _result_brief: str) -> None:  # noqa: ARG002
        if is_error:
            self.orb.flash_error()
        if skill_name == "deep_learn":
            self._active_learning = None
            self.orb.state = "thinking"

    def _on_skill_logged(self, entry: SkillLogEntry) -> None:
        self._skill_log.appendleft(entry)
        self._rebuild_skill_log_panel()

    def _rebuild_skill_log_panel(self) -> None:
        for row in self._skill_log_rows:
            row.setParent(None)
            row.deleteLater()
        self._skill_log_rows = []
        for entry in self._skill_log:
            ts = datetime.fromtimestamp(entry.ts).strftime("%H:%M:%S")
            status = "OK" if entry.ok else "FAIL"
            text = f"{ts} · {entry.skill.upper()} · {status} · {entry.ms:.0f}MS"
            label = QLabel(text, self._skill_log_panel.body)
            label.setFont(tokens.font_data(tokens.T_MICRO))
            label.setStyleSheet(f"color: {tokens.OK.name() if entry.ok else tokens.CRIT.name()};")
            self._skill_log_panel.body_layout.addWidget(label)
            self._skill_log_rows.append(label)

    def _on_learning_progress(self, topic: str, subtopic: str, progress: float) -> None:
        self.orb.set_learning_progress(subtopic, progress)
        self._active_learning = (topic, subtopic, progress)
        self._refresh_knowledge_panel()

    def _on_kb_changed(self) -> None:
        self._refresh_knowledge_panel()

    def _refresh_knowledge_panel(self) -> None:
        """§6.8: one row per learned topic, a thin bar for relative size,
        last-updated date; the active topic's row shows live progress
        instead while deep_learn is running. Empty state is an explicit
        instruction to act, not an apology (§6.8)."""
        panel = self._knowledge_panel
        for row in self._knowledge_rows:
            row.setParent(None)
            row.deleteLater()
        self._knowledge_rows = []

        rows = get_store().list_knowledge_topics()
        if not rows:
            empty = QLabel('NO TOPICS LEARNED — SAY "DEEP SEARCH ABOUT …" TO BEGIN.', panel.body)
            empty.setWordWrap(True)
            empty.setFont(tokens.font_label(tokens.T_MICRO))
            empty.setStyleSheet(f"color: {tokens.CY_600.name()};")
            panel.body_layout.addWidget(empty)
            self._knowledge_rows.append(empty)
            return

        total_chunks = sum(r["chunk_count"] for r in rows)
        header = QLabel(f"{len(rows)} TOPICS · {total_chunks} CHUNKS", panel.body)
        header.setFont(tokens.font_label(tokens.T_MICRO))
        header.setStyleSheet(f"color: {tokens.CY_500.name()};")
        panel.body_layout.addWidget(header)
        self._knowledge_rows.append(header)

        max_chunks = max((r["chunk_count"] for r in rows), default=1) or 1
        for row in rows[:3]:  # this panel's real budget fits ~3 bars; see layout.py's note
            topic = row["topic"]
            if self._active_learning and self._active_learning[0] == topic:
                _, subtopic, fraction = self._active_learning
                bar = BarMeter(f"{topic} · {subtopic}"[:28])
                bar.set_value(fraction, f"{fraction * 100:.0f}%")
            else:
                when = datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d")
                bar = BarMeter(f"{topic} — {when}"[:28])
                bar.set_value(row["chunk_count"] / max_chunks, str(row["chunk_count"]))
            panel.body_layout.addWidget(bar)
            self._knowledge_rows.append(bar)

    def _on_confirm_requested(self, question: str) -> None:
        self.set_status("confirm")
        self.show_and_activate()
        banner = ConfirmationBannerWidget(question, self)
        self._confirm_banner = banner
        self._confirm_slot.addWidget(banner)

        def answered(approved: bool) -> None:
            self._confirm_slot.removeWidget(banner)
            banner.setParent(None)
            banner.deleteLater()
            self._confirm_banner = None
            self.bridge.answer(approved)
            self.set_status("thinking")

        banner.answered.connect(answered)

    # -- commands ------------------------------------------------------

    def _on_dock_clicked(self, glyph: str, preset: str | None) -> None:
        if glyph == "SET":
            from ui.panels import SettingsDialog

            SettingsDialog(self.brain, self).exec()
            return
        if glyph == "CON":
            self.console.toggle()
            return
        self._launch_preset(preset)

    def _on_launcher_clicked(self, label: str) -> None:
        preset = LAUNCHER_PRESETS.get(label)
        if label == "LEARN":
            from ui.panels import KnowledgeDialog

            KnowledgeDialog(self).exec()
            return
        if preset:
            self._launch_preset(preset)

    def _handle_slash_command(self, cmd: str) -> None:
        command = cmd.strip().lower()
        if command == "/undo":
            self.trigger_undo()
        elif command in ("/reset", "/forget"):
            self.trigger_reset()
        elif command == "/mode voice":
            self.voice.switch_to_voice()
        elif command == "/mode text":
            self.voice.switch_to_text()
        elif command in ("/quit", "/exit"):
            QApplication.instance().quit()
        else:
            self.console.echo(f"Unknown command '{cmd}'.")

    def trigger_undo(self) -> None:
        entry = get_journal().latest()
        if entry is None:
            self.console.echo("There's nothing to undo.")
            return
        try:
            self.console.echo(get_journal().undo(entry.token))
        except Exception as e:  # noqa: BLE001 - a failed undo is a message, not a crash
            self.console.echo(f"I couldn't undo that: {e}")

    def trigger_reset(self) -> None:
        if self.current_worker is not None:
            # Brain.ask() reads self.history into a local snapshot at the
            # start of a turn and only writes it back at the end — a reset
            # while a turn is still in flight would appear to work, then be
            # silently overwritten the moment that turn's worker thread
            # finishes and persists its own (pre-reset) snapshot back.
            self.console.echo("Wait for the current action to finish before resetting.")
            return
        self.brain.reset()
        self.console.echo("Conversation memory cleared. Notes and reminders kept.")

    def announce(self, text: str) -> None:
        """Boot-time informational messages (restored history, missed
        reminders, hotkey hint) — echoed into the console's scrollback
        rather than the transcript ticker, since none of them are
        something ODIN is currently saying."""
        self.console.echo(text)

    # -- turns -----------------------------------------------------------

    def _launch_preset(self, text: str) -> None:
        """Every dock button, orb launcher, and console submission funnels
        through here — identical to Brain.ask() being sent a typed message,
        per ODIN-HUD.md §7.3. Never call a skill directly: that would bypass
        the DANGEROUS-tier confirmation gate a typed equivalent still gets."""
        if not text or self.current_worker is not None:
            return
        self.transcript_user.setText(text)
        self._process_user_turn(text)

    def _process_user_turn(self, text: str) -> None:
        self.set_status("thinking")
        self._odin_reply_parts = []
        self.transcript_odin.setText("…")

        worker = BrainWorker(self.brain, text, parent=self)
        self.current_worker = worker
        worker.turn_finished.connect(self._on_turn_finished)
        worker.error_occurred.connect(self._on_turn_error)
        worker.start()

    def _on_turn_finished(self, reply: str) -> None:
        if reply and not self._odin_reply_parts:
            self.transcript_odin.setText(reply)
        # The transcript ticker (zone E) is ODIN's primary output, but the
        # console is a self-contained typed-input surface (console.py) —
        # without this, a reply only ever appeared elsewhere on the HUD,
        # so anyone watching just the console saw their message echoed
        # and then nothing.
        if self.transcript_odin.text():
            self.console.echo(self.transcript_odin.text())
        self._finish_turn()

    def _on_turn_error(self, message: str) -> None:
        self.transcript_odin.setText(message)
        self.console.echo(message)
        self._finish_turn()

    def _finish_turn(self) -> None:
        self.current_worker = None
        self.set_status("idle")
        self.voice.notify_turn_finished()

    # -- voice mode ------------------------------------------------------

    def _on_voice_state(self, state: str) -> None:
        if self.session.mode == "voice" and self.current_worker is None:
            self.set_status(state)
            if state == "listening":
                self.voice.start_mic_meter()
            else:
                self.voice.stop_mic_meter()

    def _on_voice_heard(self, text: str) -> None:
        if self.session.mode != "voice" or self.current_worker is not None:
            # Mirrors _launch_preset's guard — without it, a voice-heard
            # turn and a dock/console-triggered turn arriving close together
            # could both start a BrainWorker, racing on self.brain's shared
            # history and the UiBridge's single confirm() Event.
            return
        self.voice.stop_mic_meter()
        self.transcript_user.setText(text)
        self._process_user_turn(text)
