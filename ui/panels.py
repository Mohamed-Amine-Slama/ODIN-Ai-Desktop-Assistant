"""Secondary HUD dialogs: settings/skills and the knowledge base browser.

Kept out of app_window.py — that file is already the HUD's main window; these
are supporting, occasionally-opened panels with their own state, styled to
match but independent of it.
"""
import json

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import config
from core import research
from core.env_file import update_env
from core.store import get_store

_PANEL_STYLESHEET = """
QDialog {
    background-color: #05090f;
}
QWidget {
    color: #d6f5f3;
    font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
    font-size: 13px;
}
QLabel#panelTitle {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 3px;
    color: #cffafe;
}
QLabel#sectionHead {
    font-family: 'Consolas', monospace;
    font-size: 11px;
    letter-spacing: 2px;
    color: #67e8f9;
}
QFrame#row {
    background-color: rgba(9, 16, 28, 0.55);
    border: 1px solid rgba(34, 211, 238, 0.20);
    border-radius: 3px;
}
QListWidget {
    background: transparent;
    border: none;
}
QListWidget::item {
    padding: 6px 4px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.10);
}
QLineEdit {
    background: rgba(9, 16, 28, 0.6);
    border: 1px solid rgba(34, 211, 238, 0.30);
    border-radius: 3px;
    padding: 6px 10px;
    color: #ecfeff;
}
QPushButton {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(34, 211, 238, 0.25);
    border-radius: 3px;
    color: #a5f3fc;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: rgba(34, 211, 238, 0.20);
    border: 1px solid rgba(34, 211, 238, 0.65);
    color: #ecfeff;
}
QPushButton:disabled {
    color: #475569;
    border: 1px solid rgba(148, 163, 184, 0.12);
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid rgba(34, 211, 238, 0.45);
    border-radius: 3px;
    background: rgba(9, 16, 28, 0.6);
}
QCheckBox::indicator:checked {
    background: rgba(34, 211, 238, 0.85);
    border: 1px solid rgba(34, 211, 238, 0.9);
}
"""


class SettingsDialog(QDialog):
    """Read-only view of what's registered, plus the few toggles that are
    safe to flip from a running session."""

    def __init__(self, brain, parent=None):
        super().__init__(parent)
        self.brain = brain
        self.setWindowTitle(f"{config.ASSISTANT_NAME} — Settings")
        self.resize(560, 640)
        self.setStyleSheet(_PANEL_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("SETTINGS", self)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        info = QLabel(
            f"Model: {config.MODEL}\nEffort: {config.EFFORT}\nEndpoint: {config.BASE_URL or '(Anthropic native)'}",
            self,
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #94a3b8; font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(info)

        layout.addWidget(self._toggles_section())
        layout.addWidget(self._skills_section(), 1)

        footer = QLabel(
            "Shell and input-control changes take effect on next launch. "
            "The confirmation toggle applies immediately.",
            self,
        )
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(footer)

        close_btn = QPushButton("CLOSE", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _toggles_section(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("row")
        col = QVBoxLayout(frame)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)

        head = QLabel("BEHAVIOUR", frame)
        head.setObjectName("sectionHead")
        col.addWidget(head)

        confirm_check = QCheckBox("Ask before dangerous actions", frame)
        confirm_check.setChecked(config.CONFIRM_DESTRUCTIVE)
        confirm_check.toggled.connect(self._on_confirm_toggled)
        col.addWidget(confirm_check)

        shell_check = QCheckBox("Shell commands enabled (restart to apply)", frame)
        shell_check.setChecked(config.ENABLE_SHELL)
        shell_check.toggled.connect(lambda checked: update_env({"ENABLE_SHELL": "1" if checked else "0"}))
        col.addWidget(shell_check)

        input_check = QCheckBox("Keyboard/mouse control enabled (restart to apply)", frame)
        input_check.setChecked(config.ENABLE_INPUT_CONTROL)
        input_check.toggled.connect(
            lambda checked: update_env({"ENABLE_INPUT_CONTROL": "1" if checked else "0"})
        )
        col.addWidget(input_check)

        return frame

    @staticmethod
    def _on_confirm_toggled(checked: bool) -> None:
        # Brain._run_tools reads config.CONFIRM_DESTRUCTIVE fresh on every
        # call, so mutating the module attribute takes effect on the very
        # next tool call — no restart, no brain reconstruction needed.
        config.CONFIRM_DESTRUCTIVE = checked
        update_env({"CONFIRM_DESTRUCTIVE": "1" if checked else "0"})

    def _skills_section(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("row")
        col = QVBoxLayout(frame)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)

        skills = sorted(self.brain.skills.skills.values(), key=lambda s: s.name)
        head = QLabel(f"REGISTERED SKILLS ({len(skills)})", frame)
        head.setObjectName("sectionHead")
        col.addWidget(head)

        listing = QListWidget(frame)
        for skill in skills:
            listing.addItem(QListWidgetItem(f"{skill.name} — {skill.description}"))
        col.addWidget(listing, 1)

        return frame


class KnowledgeWorker(QThread):
    """Runs one deep_learn pass off the GUI thread — it can take a minute or
    two of back-to-back web research, and the dialog needs to keep painting
    (and streaming progress) while that happens."""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, topic: str, depth: str, parent=None):
        super().__init__(parent)
        self.topic = topic
        self.depth = depth

    def run(self) -> None:
        try:
            result = research.run_deep_learn(self.topic, depth=self.depth, progress=self.progress.emit)
        except research.ResearchError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:  # noqa: BLE001 - a failed run must not kill the HUD
            self.failed.emit(f"Research failed: {e}")
            return
        self.finished_ok.emit(result)


class KnowledgeDialog(QDialog):
    """Browse what deep_learn has already researched, and kick off a new
    research pass on a topic."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{config.ASSISTANT_NAME} — Knowledge")
        self.resize(600, 660)
        self.setStyleSheet(_PANEL_STYLESHEET)
        self._worker: KnowledgeWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("KNOWLEDGE", self)
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Deep-learn a topic and it's remembered permanently — future "
            "questions about it are answered from these notes.",
            self,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(subtitle)

        learn_row = QHBoxLayout()
        self.topic_field = QLineEdit(self)
        self.topic_field.setPlaceholderText("Topic to deep-learn, e.g. 'React hooks'")
        self.topic_field.returnPressed.connect(self._start_learning)
        learn_row.addWidget(self.topic_field, 1)
        self.learn_btn = QPushButton("LEARN", self)
        self.learn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.learn_btn.clicked.connect(self._start_learning)
        learn_row.addWidget(self.learn_btn, 0)
        layout.addLayout(learn_row)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color: #7dd3fc; font-family: Consolas, monospace; font-size: 12px;"
        )
        layout.addWidget(self.status_label)

        head = QLabel("LEARNED TOPICS", self)
        head.setObjectName("sectionHead")
        layout.addWidget(head)

        self.topics_list = QListWidget(self)
        layout.addWidget(self.topics_list, 1)

        close_btn = QPushButton("CLOSE", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._refresh_topics()

    def _refresh_topics(self) -> None:
        self.topics_list.clear()
        rows = get_store().list_knowledge_topics()
        if not rows:
            self.topics_list.addItem("Nothing deep-learned yet.")
            return
        for row in rows:
            subtopics = json.loads(row["subtopics"])
            text = f"{row['topic']} — {row['chunk_count']} notes — {', '.join(subtopics)}"
            self.topics_list.addItem(text)

    def _start_learning(self) -> None:
        topic = self.topic_field.text().strip()
        if not topic or self._worker is not None:
            return

        problem = research.preflight()
        if problem:
            QMessageBox.warning(self, "Can't start", problem)
            return

        self.learn_btn.setEnabled(False)
        self.topic_field.setEnabled(False)
        self.status_label.setText(f"Starting research on '{topic}'…")

        self._worker = KnowledgeWorker(topic, "standard", self)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished_ok.connect(self._on_learn_finished)
        self._worker.failed.connect(self._on_learn_failed)
        self._worker.start()

    def _on_learn_finished(self, result: dict) -> None:
        self.status_label.setText(
            f"Done — learned {len(result['subtopics'])} subtopic(s), "
            f"filled {len(result['gaps_filled'])} gap(s), stored {result['chunks_added']} notes."
        )
        self._worker = None
        self.learn_btn.setEnabled(True)
        self.topic_field.setEnabled(True)
        self.topic_field.clear()
        self._refresh_topics()

    def _on_learn_failed(self, message: str) -> None:
        self.status_label.setText(message)
        self._worker = None
        self.learn_btn.setEnabled(True)
        self.topic_field.setEnabled(True)
