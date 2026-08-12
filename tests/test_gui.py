"""HUD tests. Run headless via Qt's offscreen platform, so they work in WSL."""
import os
import threading
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest  # noqa: E402
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


def test_tool_call_retires_the_live_bubble_so_narration_does_not_pile_up(window):
    """Regression: every sentence spoken across an entire multi-tool-call turn
    used to pile into one ever-growing bubble, with no chronological relation
    to the activity log entries interleaved between them — a widget that
    could grow to hundreds of lines, resized repeatedly, was what produced
    the garbled/overlapping HUD layout. A tool call must close out whatever
    bubble was open so the next narration starts fresh."""
    window._live_text = []
    window._live_label = window.append_jarvis_message("…")

    window._on_chunk("About to do something.")
    first_bubble = window._live_label
    assert first_bubble.text() == "About to do something."

    window._on_tool_started("click", "x=1, y=2")
    assert window._live_label is None, "the bubble must be retired, not kept growing"

    window._on_chunk("Now doing the next thing.")
    second_bubble = window._live_label
    assert second_bubble is not first_bubble, "narration after a tool call must be a new bubble"
    assert second_bubble.text() == "Now doing the next thing."
    # The first bubble keeps its own text rather than absorbing later chunks.
    assert first_bubble.text() == "About to do something."


def test_empty_placeholder_bubble_is_collapsed_not_left_stray(window):
    """If the model calls a tool with no narration first, the '…' placeholder
    must not linger in the transcript as a stray empty bubble."""
    window._live_text = []
    window._live_label = window.append_jarvis_message("…")
    placeholder = window._live_label

    window._on_tool_started("open_app", "opera gx")

    assert not placeholder.parentWidget().isVisible()


def test_final_reply_gets_a_bubble_even_after_the_live_one_was_retired(window):
    """Regression: if the turn's last step was a tool call, _live_label was
    already None by the time the turn finished, and the final reply text was
    silently dropped instead of shown."""
    window._live_text = []
    window._live_label = window.append_jarvis_message("…")
    window._on_tool_started("click", "")  # retires the placeholder, no narration since

    before = window.chat_layout.count()
    window._on_turn_finished("All done.")

    assert window.chat_layout.count() > before
    assert window._live_label is None  # _finish_turn resets it for the next turn


def test_bubble_width_is_capped_by_the_actual_panel_not_a_fixed_constant(window):
    """Regression: _BUBBLE_MAX_WIDTH (680) is only ever an upper bound. On a
    panel narrower than that — a smaller window, a lower-resolution or scaled
    display — a bubble clamped to the bare constant overflowed straight past
    the panel's own right edge instead of wrapping within it."""
    window.resize(500, 500)
    window.show()
    QTest.qWait(50)

    long_text = (
        'open opera GX the instagram, find ninorz in my DMs and send him a '
        'message that says "waaaaa rojla"'
    )
    label = window.append_user_message(long_text)
    QTest.qWait(50)

    frame = label.parentWidget()
    viewport_width = window.scroll_area.viewport().width()
    assert viewport_width > 0
    assert frame.width() <= viewport_width


def test_intro_message_waits_for_a_real_show_and_is_sized_correctly(qapp, mock_brain, mock_session):
    """Regression: the intro message used to be sent from __init__, before the
    window was ever shown. Qt doesn't lay out hidden widgets, so the scroll
    area's viewport had no real width yet — the bubble fell back to a width
    wider than this app's viewport legitimately ever is (the orb column eats
    most of the window), overflowing past the panel on every single launch."""
    from ui.app_window import JarvisMainWindow

    win = JarvisMainWindow(mock_brain, mock_session)
    try:
        assert win.chat_layout.count() == 1, "nothing sent before a real show()"

        win.show_and_activate()
        QTest.qWait(50)

        assert win.chat_layout.count() == 2  # the stretch, plus the intro bubble
        row = win.chat_layout.itemAt(0).layout()
        frame = row.itemAt(0).widget()
        assert frame.width() <= win.scroll_area.viewport().width()

        # Summoning it again must not repeat the greeting.
        win.show_and_activate()
        QTest.qWait(20)
        assert win.chat_layout.count() == 2
    finally:
        win.orb.stop()
        win.tray_icon.hide()
        win.deleteLater()


def test_bubble_max_width_falls_back_before_the_window_is_laid_out():
    """Before the scroll area has ever been shown, its viewport reports width
    0 — the fallback must be the constant, not a degenerate 0-width cap."""
    from unittest.mock import MagicMock

    from ui.app_window import JarvisMainWindow, _BUBBLE_MAX_WIDTH

    win = JarvisMainWindow(MagicMock(), MagicMock())
    try:
        assert win._bubble_max_width() == _BUBBLE_MAX_WIDTH
    finally:
        win.deleteLater()


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
