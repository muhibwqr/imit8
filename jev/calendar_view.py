"""The calendar window: drag a rectangle over a week grid to schedule a task."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .flows import FlowStore
from .schedule import DAYS, SLOT_MINUTES, SLOTS_PER_DAY, Schedule, ScheduleStore, slot_label

GUTTER = 56
HEADER = 26
CELL_H = 15

STYLE = """
#calendar { background: #14151a; }
QLabel { color: #cfd3e2; font-size: 13px; }
QLabel#muted { color: #7a8095; font-size: 12px; }
QLineEdit, QComboBox {
    color: #f5f6fa; background: #1d1f28; border: 1px solid #2c2f3a;
    border-radius: 10px; padding: 8px 10px; font-size: 14px;
}
QPushButton {
    color: #e7e9f5; background: #232634; border: 1px solid #343849;
    border-radius: 10px; padding: 8px 14px; font-size: 13px;
}
QPushButton:hover { border-color: #4a5cff; }
QPushButton#primary { background: #3b49d6; border-color: #4a5cff; }
QCheckBox { color: #cfd3e2; font-size: 12px; }
"""


class SlotGrid(QWidget):
    """A week × 30-minute grid you paint by dragging."""

    changed = Signal()

    def __init__(self, start_hour: int = 6, end_hour: int = 24) -> None:
        super().__init__()
        self.start_slot = start_hour * 60 // SLOT_MINUTES
        self.end_slot = min(end_hour * 60 // SLOT_MINUTES, SLOTS_PER_DAY)
        self.selected: set[tuple[int, int]] = set()
        self._drag_from: tuple[int, int] | None = None
        self._drag_to: tuple[int, int] | None = None
        self._erasing = False
        self.setMouseTracking(True)
        self.setMinimumWidth(620)
        self.setMinimumHeight(HEADER + (self.end_slot - self.start_slot) * CELL_H + 4)

    # --- geometry ----------------------------------------------------------
    @property
    def rows(self) -> int:
        return self.end_slot - self.start_slot

    def _col_width(self) -> float:
        return max((self.width() - GUTTER) / 7.0, 1.0)

    def _cell_at(self, x: int, y: int) -> tuple[int, int] | None:
        if x < GUTTER or y < HEADER:
            return None
        day = int((x - GUTTER) // self._col_width())
        row = int((y - HEADER) // CELL_H)
        if not (0 <= day < 7 and 0 <= row < self.rows):
            return None
        return day, self.start_slot + row

    def _cell_rect(self, day: int, slot: int) -> QRect:
        width = self._col_width()
        return QRect(
            int(GUTTER + day * width) + 1,
            HEADER + (slot - self.start_slot) * CELL_H + 1,
            int(width) - 2,
            CELL_H - 2,
        )

    def _drag_cells(self) -> set[tuple[int, int]]:
        if not (self._drag_from and self._drag_to):
            return set()
        (d1, s1), (d2, s2) = self._drag_from, self._drag_to
        return {
            (d, s)
            for d in range(min(d1, d2), max(d1, d2) + 1)
            for s in range(min(s1, s2), max(s1, s2) + 1)
        }

    # --- interaction -------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        cell = self._cell_at(event.position().x(), event.position().y())
        if cell is None:
            return
        self._erasing = cell in self.selected
        self._drag_from = self._drag_to = cell
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._drag_from is None:
            return
        cell = self._cell_at(event.position().x(), event.position().y())
        if cell is not None:
            self._drag_to = cell
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802, ARG002 - Qt naming
        cells = self._drag_cells()
        if cells:
            if self._erasing:
                self.selected -= cells
            else:
                self.selected |= cells
            self.changed.emit()
        self._drag_from = self._drag_to = None
        self.update()

    # --- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802, ARG002 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#14151a"))
        width = self._col_width()
        pending = self._drag_cells()

        painter.setFont(QFont("Sans", 8))
        painter.setPen(QPen(QColor("#7a8095")))
        for day in range(7):
            painter.drawText(
                QRect(int(GUTTER + day * width), 4, int(width), HEADER - 6),
                Qt.AlignCenter,
                DAYS[day],
            )
        for slot in range(self.start_slot, self.end_slot):
            y = HEADER + (slot - self.start_slot) * CELL_H
            if slot * SLOT_MINUTES % 60 == 0:
                painter.setPen(QPen(QColor("#7a8095")))
                painter.drawText(
                    QRect(0, y, GUTTER - 8, CELL_H),
                    Qt.AlignRight | Qt.AlignVCenter,
                    slot_label(slot * SLOT_MINUTES),
                )

        for slot in range(self.start_slot, self.end_slot):
            for day in range(7):
                rect = self._cell_rect(day, slot)
                cell = (day, slot)
                on = cell in self.selected
                if cell in pending:
                    on = not self._erasing
                if on:
                    painter.fillRect(rect, QColor("#3b49d6"))
                else:
                    on_hour = slot * SLOT_MINUTES % 60 == 0
                    painter.fillRect(rect, QColor("#1b1d26" if on_hour else "#181a22"))

    # --- values ------------------------------------------------------------
    def slots(self) -> list[tuple[int, int]]:
        return sorted((day, slot * SLOT_MINUTES) for day, slot in self.selected)

    def set_slots(self, slots: list[tuple[int, int]]) -> None:
        self.selected = {(day, minute // SLOT_MINUTES) for day, minute in slots}
        self.update()

    def clear(self) -> None:
        self.selected.clear()
        self.changed.emit()
        self.update()


def _relative(when: float) -> str:
    delta = when - time.time()
    if delta < 0:
        return "now"
    hours, minutes = divmod(int(delta // 60), 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"


class CalendarWindow(QWidget):
    """Pick a task, drag its time slots, save. Existing schedules listed below."""

    saved = Signal()

    def __init__(self, store: ScheduleStore | None = None, flows: FlowStore | None = None) -> None:
        super().__init__()
        self.store = store or ScheduleStore()
        self.flows = flows or FlowStore()
        self.setObjectName("calendar")
        self.setWindowTitle("jev — schedule")
        self.setStyleSheet(STYLE)
        self.resize(820, 700)
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        row = QHBoxLayout()
        self.task = QLineEdit()
        self.task.setPlaceholderText("what should jev do?")
        row.addWidget(self.task, 3)
        self.known = QComboBox()
        self.known.addItem("pick a flow…", "")
        for flow in self.flows.all_flows():
            self.known.addItem(f"{flow.task} ({flow.badge()})", flow.task)
        self.known.currentIndexChanged.connect(self._pick_flow)
        row.addWidget(self.known, 2)
        layout.addLayout(row)

        hint = QLabel("drag across the grid to pick times · drag over blue cells to clear them")
        hint.setObjectName("muted")
        layout.addWidget(hint)

        self.grid = SlotGrid()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.grid)
        layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        self.summary = QLabel("no slots selected")
        self.summary.setObjectName("muted")
        self.grid.changed.connect(self._update_summary)
        actions.addWidget(self.summary, 1)
        clear = QPushButton("clear")
        clear.clicked.connect(self.grid.clear)
        actions.addWidget(clear)
        save = QPushButton("schedule it")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        layout.addLayout(actions)

        self.list_label = QLabel("scheduled")
        layout.addWidget(self.list_label)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        layout.addLayout(self.rows)

    def _pick_flow(self) -> None:
        task = self.known.currentData()
        if task:
            self.task.setText(task)

    def _update_summary(self) -> None:
        count = len(self.grid.selected)
        self.summary.setText("no slots selected" if not count else f"{count} slots selected")

    def save(self) -> None:
        task = self.task.text().strip()
        slots = self.grid.slots()
        if not task or not slots:
            self.summary.setText("type a task and drag at least one slot")
            return
        self.store.add(task, slots=slots)
        self.task.clear()
        self.grid.clear()
        self.refresh()
        self.saved.emit()

    def refresh(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())
        for schedule in self.store.all():
            self.rows.addLayout(self._schedule_row(schedule))
        self._update_summary()

    def _schedule_row(self, schedule: Schedule) -> QHBoxLayout:
        row = QHBoxLayout()
        when = schedule.next_run()
        next_text = (
            f"next {datetime.fromtimestamp(when).strftime('%a %H:%M')} ({_relative(when)})"
            if when
            else "not scheduled"
        )
        label = QLabel(f"{schedule.task}\n{schedule.describe()} · {next_text}")
        row.addWidget(label, 1)

        toggle = QCheckBox("on")
        toggle.setChecked(schedule.enabled)
        toggle.toggled.connect(
            lambda on, sid=schedule.id: (self.store.set_enabled(sid, on), self.refresh())
        )
        row.addWidget(toggle)

        edit = QPushButton("edit")
        edit.clicked.connect(lambda _=False, s=schedule: self._load(s))
        row.addWidget(edit)

        delete = QPushButton("delete")
        delete.clicked.connect(
            lambda _=False, sid=schedule.id: (self.store.remove(sid), self.refresh())
        )
        row.addWidget(delete)
        return row

    def _load(self, schedule: Schedule) -> None:
        self.task.setText(schedule.task)
        self.grid.set_slots(schedule.slots)
        self.store.remove(schedule.id)
        self.refresh()


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())
