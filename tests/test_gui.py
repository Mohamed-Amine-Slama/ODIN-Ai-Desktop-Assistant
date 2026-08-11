"""HUD tests. Run headless via Qt's offscreen platform, so they work in WSL."""
import os
import threading
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.ask.return_value = "Hello from Jarvis!"
    return brain


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.mode = "text"
    session.set_mode.return_value = "Switched mode."
    return session


@pytest.fixture
def window(qapp, mock_brain, mock_session):
    from ui.app_window import JarvisMainWindow

    win = JarvisMainWindow(mock_brain, mock_session)
    yield win
    win.orb.stop()
    win.tray_icon.hide()
    win.deleteLater()


# -- small widgets ---------------------------------------------------------


def test_action_card_offers_undo_only_when_there_is_a_token(qapp):
    """The tiered model promises undo on MODERATE actions, but typing and
    closing a window genuinely cannot be reversed. No token, no button."""
    from PyQt6.QtWidgets import QPushButton

    from ui.app_window import ActionCardWidget

    received = []
    reversible = ActionCardWidget("write_file", "token-123", "Restore notes.txt")
    reversible.undo_requested.connect(received.append)
    reversible._on_undo_click()
    assert received == ["token-123"]
    assert reversible.findChildren(QPushButton)

    permanent = ActionCardWidget("type_text", "", "")
    assert not permanent.findChildren(QPushButton)


def test_confirmation_banner_emits_both_answers(qapp):
    from ui.app_window import ConfirmationBannerWidget

    received = []
    banner = ConfirmationBannerWidget("Delete the folder?")
    banner.answered.connect(received.append)
    banner.answered.emit(True)
    banner.answered.emit(False)
    assert received == [True, False]


# -- the HUD ---------------------------------------------------------------


def test_hud_builds_and_renders_messages(window):
    import config

    assert window.windowTitle() == f"{config.ASSISTANT_NAME} — Personal AI Desktop Assistant"

    before = window.chat_layout.count()
    window.append_user_message("Test user query")
    window.append_action_card("write_file", "tok-1", "Restore notes.txt")
    assert window.chat_layout.count() == before + 2


def test_model_output_is_never_rendered_as_markup(window):
    """Jarvis reads files and shell output. If a bubble rendered rich text, a
    file containing markup would decide what the user sees."""
    from PyQt6.QtCore import Qt

    label = window.append_jarvis_message("<b>not bold</b> & <script>")
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == "<b>not bold</b> & <script>"

    ours = window.append_jarvis_message("<b>bold</b>", rich=True)
    assert ours.textFormat() == Qt.TextFormat.RichText


def test_status_drives_the_orb_and_emits_for_the_desktop_orb(window):
    seen = []
    window.state_changed.connect(seen.append)

    window.set_status("thinking")
    assert window.orb.state == "thinking"
    window.set_status("confirm")
    assert window.orb.state == "thinking"  # confirm keeps the orb agitated
    window.set_status("idle")
    assert window.orb.state == "idle"
    assert seen == ["thinking", "thinking", "idle"]


def test_input_is_locked_for_the_duration_of_a_turn(window, monkeypatch):
    """Regression: only the Send button used to be disabled, so pressing Enter
    started a second turn that interleaved with the first."""
    started = []
    monkeypatch.setattr(
        "ui.app_window.BrainWorker.start", lambda self: started.append(self.user_text)
    )

    window.input_field.setText("first")
    window._on_send_click()
    assert started == ["first"]
    assert not window.input_field.isEnabled()

    window.input_field.setText("second")
    window._on_send_click()
    assert started == ["first"], "a second turn must not start mid-turn"

    window._finish_turn()
    assert window.input_field.isEnabled()


def test_streamed_sentences_accumulate_into_one_bubble(window):
    window._live_text = []
    window._live_label = window.append_jarvis_message("…")
    window._on_chunk("First sentence.")
    window._on_chunk("Second one.")
    assert window._live_label.text() == "First sentence. Second one."


def test_hud_toggle_flips_always_on_top(window):
    start = window.is_hud_always_on_top
    window._toggle_hud_mode()
    assert window.is_hud_always_on_top is not start
    window._toggle_hud_mode()
    assert window.is_hud_always_on_top is start


def test_unknown_command_says_so_instead_of_silently_ignoring_it(window):
    before = window.chat_layout.count()
    window._handle_slash_command("/wat")
    assert window.chat_layout.count() > before


# -- the bridge ------------------------------------------------------------


def test_bridge_speaks_and_displays_every_sentence(qapp):
    """Regression: the GUI replaced the brain's on_text callback with one that
    only drew to the screen, which left the desktop app completely mute."""
    from ui.workers import UiBridge

    speaker = MagicMock()
    bridge = UiBridge(speaker=speaker)
    seen = []
    bridge.text_chunk.connect(seen.append)

    bridge.on_text("All done.")

    speaker.say.assert_called_once_with("All done.")
    assert seen == ["All done."]


def test_bridge_confirmation_blocks_until_answered(qapp):
    from ui.workers import UiBridge

    bridge = UiBridge()
    skill = MagicMock()
    skill.consequence.return_value = "Delete it?"

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=bridge.confirm(skill, {})))
    worker.start()
    # The worker is parked on the Event until the GUI thread answers.
    assert not bridge._answered.wait(timeout=0.1)
    bridge.answer(True)
    worker.join(timeout=5)

    assert result == {"ok": True}


def test_bridge_confirmation_defaults_to_no_on_timeout(qapp, monkeypatch):
    """Nothing is refused outright, but an unanswered question is not consent."""
    import config

    from ui.workers import UiBridge

    monkeypatch.setattr(config, "CONFIRM_TIMEOUT_SECONDS", 0.05)
    bridge = UiBridge()
    skill = MagicMock()
    skill.consequence.return_value = "Format the drive?"

    assert bridge.confirm(skill, {}) is False


def test_bridge_reports_undo_token_only_when_one_exists(qapp):
    from core.undo import UndoJournal, set_journal
    from ui.workers import UiBridge

    journal = UndoJournal()
    set_journal(journal)
    try:
        bridge = UiBridge()
        seen = []
        bridge.action_reported.connect(lambda *args: seen.append(args))

        skill = MagicMock()
        skill.name = "type_text"
        bridge.on_action(skill, {}, MagicMock(undo_token=None))
        assert seen[-1] == ("type_text", "", "")

        token = journal.record("Restore notes.txt", lambda: "back")
        skill.name = "write_file"
        bridge.on_action(skill, {}, MagicMock(undo_token=token))
        assert seen[-1] == ("write_file", token, "Restore notes.txt")
    finally:
        set_journal(None)


# -- the orb ---------------------------------------------------------------


def test_orb_swarm_reacts_to_state(qapp):
    """The whole point of the swarm: tight when idle, scattered when working."""
    from ui.orb import ReactorOrb

    orb = ReactorOrb()
    orb.stop()

    orb.state = "idle"
    idle_spread = max(p.target for p in orb._particles) - min(p.target for p in orb._particles)
    orb.state = "thinking"
    thinking_spread = max(p.target for p in orb._particles) - min(p.target for p in orb._particles)

    assert thinking_spread > idle_spread * 2
    orb.deleteLater()


def test_orb_renders_in_every_state(qapp):
    """paintEvent runs a lot of geometry; a crash in it takes the HUD down."""
    from PyQt6.QtGui import QPixmap

    from ui.orb import STATE_STYLE, ReactorOrb

    orb = ReactorOrb()
    orb.resize(240, 240)
    for state in STATE_STYLE:
        orb.state = state
        orb._tick()
        pixmap = QPixmap(240, 240)
        orb.render(pixmap)
    orb.stop()
    orb.deleteLater()
