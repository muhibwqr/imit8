"""The 'wyd?' spotlight window.

Launch it from the dock, type a task, hit enter. Flows you have run before show
up as chips ("used 4 times") that re-run on a single click.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .agent import Agent, Event
from .flows import Flow

STYLE = """
#card { background: #14151a; border: 1px solid #2c2f3a; border-radius: 18px; }
#prompt { color: #f5f6fa; font-size: 26px; border: none; background: transparent; }
#hint { color: #6f7486; font-size: 12px; }
#status { color: #9aa0b4; font-size: 13px; }
QPushButton#chip {
    color: #dfe2ee; background: #1d1f28; border: 1px solid #2c2f3a;
    border-radius: 14px; padding: 7px 12px; font-size: 13px; text-align: left;
}
QPushButton#chip:hover { background: #262935; border-color: #4a5cff; }
QPushButton#stop {
    color: #ffd7d7; background: #3a1d22; border: 1px solid #6b2b33;
    border-radius: 12px; padding: 6px 12px; font-size: 12px;
}
"""


HOTKEY = "<cmd>+<shift>+space" if platform.system() == "Darwin" else "<ctrl>+<alt>+space"


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#14151a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#7a8cff"))
    painter.setFont(QFont("Sans", 30, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "j")
    painter.end()
    return QIcon(pixmap)


class Hotkey(QObject):
    """Global shortcut that summons the spotlight from anywhere."""

    triggered = Signal()

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 - optional dependency / no input perms
            return False
        try:
            self._listener = keyboard.GlobalHotKeys({HOTKEY: self.triggered.emit})
            self._listener.daemon = True
            self._listener.start()
        except Exception:  # noqa: BLE001 - e.g. missing accessibility permission
            return False
        return True


class Runner(QThread):
    event = Signal(object)
    finished_run = Signal(object)

    def __init__(self, agent: Agent, task: str = "", flow: Flow | None = None) -> None:
        super().__init__()
        self.agent = agent
        self.task = task
        self.flow = flow

    def run(self) -> None:  # noqa: D102
        sink: Callable[[Event], None] = self.event.emit
        try:
            if self.flow is not None:
                result = self.agent.replay(self.flow, sink)
            else:
                result = self.agent.run(self.task, sink)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.event.emit(Event("error", str(exc)))
            return
        self.finished_run.emit(result)


class Spotlight(QWidget):
    def __init__(self, agent: Agent | None = None) -> None:
        super().__init__()
        self.agent = agent or Agent()
        self.runner: Runner | None = None
        self._build()
        self.refresh_flows()

    # --- layout ------------------------------------------------------------
    def _build(self) -> None:
        self.setWindowTitle("jev")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.resize(660, 200)

        card = QFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        self.prompt = QLineEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("wyd?")
        self.prompt.returnPressed.connect(self.start_task)
        layout.addWidget(self.prompt)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.hide()
        layout.addWidget(self.status)

        self.chips = QVBoxLayout()
        self.chips.setSpacing(6)
        layout.addLayout(self.chips)

        footer = QHBoxLayout()
        hint = QLabel("enter to run  ·  esc to hide  ·  click a flow to replay it")
        hint.setObjectName("hint")
        footer.addWidget(hint)
        footer.addStretch(1)
        self.stop_button = QPushButton("stop")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop_run)
        self.stop_button.hide()
        footer.addWidget(self.stop_button)
        layout.addLayout(footer)

        QShortcut(QKeySequence("Escape"), self, activated=self.hide)

    def refresh_flows(self) -> None:
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for flow in self.agent.store.suggestions():
            bolt = "⚡ " if flow.replayable else ""
            button = QPushButton(f"{bolt}{flow.task}     · {flow.badge()}, run it again?")
            button.setObjectName("chip")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, f=flow: self.start_flow(f))
            self.chips.addWidget(button)
        self.adjustSize()

    # --- running -----------------------------------------------------------
    def start_task(self) -> None:
        task = self.prompt.text().strip()
        if task:
            self._start(Runner(self.agent, task=task), f"running: {task}")

    def start_flow(self, flow: Flow) -> None:
        runner = Runner(self.agent, task=flow.task, flow=flow if flow.replayable else None)
        verb = "replaying" if flow.replayable else "running"
        self._start(runner, f"{verb}: {flow.task}")

    def _start(self, runner: Runner, label: str) -> None:
        if self.runner and self.runner.isRunning():
            return
        self.prompt.clear()
        self.prompt.setEnabled(False)
        self.status.setText(label)
        self.status.show()
        self.stop_button.show()
        self.runner = runner
        runner.event.connect(self.on_event)
        runner.finished_run.connect(self.on_finished)
        self.showMinimized()  # get out of the agent's way
        runner.start()

    def stop_run(self) -> None:
        self.agent.stop()
        self.status.setText("stopping…")

    def on_event(self, event: Event) -> None:
        if event.kind in {"action", "step", "error", "done", "flow"}:
            self.status.setText(event.text)

    def on_finished(self, result) -> None:  # noqa: ANN001 - agent.Result
        self.prompt.setEnabled(True)
        self.stop_button.hide()
        self.status.setText(f"{result.status} · {result.steps} steps · {result.duration:.1f}s")
        self.refresh_flows()
        self.show_spotlight()

    def show_spotlight(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.center().x() - self.width() // 2, int(screen.height() * 0.22))
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("jev")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    window = Spotlight()

    tray = QSystemTrayIcon(app_icon(), app)
    tray.setToolTip("jev — wyd?")
    menu = QMenu()
    open_action = QAction("wyd?", menu)
    open_action.triggered.connect(window.show_spotlight)
    quit_action = QAction("quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(open_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show_spotlight()
        if reason == QSystemTrayIcon.Trigger
        else None
    )
    tray.show()

    hotkey = Hotkey()
    hotkey.triggered.connect(window.show_spotlight)
    hotkey.start()

    window.show_spotlight()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
