"""The 'wyd?' spotlight window.

Launch it from the dock, type a task, hit enter. Flows you have run before show
up as chips ("used 4 times") that re-run on a single click.
"""

from __future__ import annotations

import io
import os
import platform
import sys
import time
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QRegion,
    QShortcut,
)
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

from .agent import Agent, Event, Result
from .calendar_view import CalendarWindow
from .config import HOME_DIR
from .flows import Flow
from .hotkey import ModifierChord
from .schedule import Schedule, ScheduleStore
from .scheduler import Scheduler
from .windows import ScopedWindow, app_icon_png, frontmost_window

ACCENT_FALLBACK = "#0a84ff"  # macOS default control accent


def _accent() -> str:
    """The user's system accent color (HIG: controlAccentColor), via the palette."""
    try:
        color = QApplication.palette().color(QPalette.ColorRole.Accent)
        if color.isValid():
            return color.name()
    except Exception:  # noqa: BLE001 - older Qt / headless
        pass
    return ACCENT_FALLBACK


def _style(glass: bool = False) -> str:
    """The panel stylesheet.

    HIG-fluent: one accent (the system's own), semantic-looking label colors,
    white-alpha fills that composite properly over a vibrancy material, and a
    :focus rule so keyboard navigation is visible.
    """
    accent = _accent()
    card_bg = "rgba(24, 26, 34, 120)" if glass else "#14151a"
    card_edge = "rgba(255, 255, 255, 0.14)" if glass else "#2c2f3a"
    return f"""
#card {{ background: {card_bg}; border: 1px solid {card_edge}; border-radius: 20px; }}
#prompt {{ color: #f5f6fa; font-size: 26px; border: none; background: transparent; }}
#prompt:focus {{ border: none; }}
#hint {{ color: rgba(235, 238, 250, 0.45); font-size: 12px; }}
#status {{ color: rgba(235, 238, 250, 0.62); font-size: 13px; }}
QPushButton#chip {{
    color: #f0f2fa; background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.13);
    border-radius: 9px; padding: 8px 12px; font-size: 13px; text-align: left;
}}
QPushButton#chip:hover {{ background: rgba(255, 255, 255, 0.12); border-color: rgba(255, 255, 255, 0.28); }}
QPushButton#chip:focus {{ border: 1px solid {accent}; background: rgba(255, 255, 255, 0.12); }}
#upcoming {{ color: rgba(235, 238, 250, 0.55); font-size: 12px; }}
QPushButton#ghost {{
    color: rgba(235, 238, 250, 0.72); background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px; padding: 5px 11px; font-size: 12px;
}}
QPushButton#ghost:hover {{ background: rgba(255, 255, 255, 0.10); color: #f5f6fa; }}
QPushButton#ghost:focus {{ border: 1px solid {accent}; }}
QPushButton#stop {{
    color: #ffd7d7; background: rgba(224, 85, 97, 0.16);
    border: 1px solid rgba(224, 85, 97, 0.45);
    border-radius: 8px; padding: 5px 11px; font-size: 12px;
}}
QPushButton#stop:hover {{ background: rgba(224, 85, 97, 0.26); }}
"""


# ctrl+option(alt)+space, or just tap ctrl+option — option is alt on macOS keyboards.
HOTKEY = "<ctrl>+<alt>+<space>"
HOTKEY_LABEL = (
    "ctrl+option · ×2 scopes the front window"
    if platform.system() == "Darwin"
    else "ctrl+alt · ×2 scopes the front window"
)
# Qt maps "Ctrl" to the command key on macOS ("Meta" would be physical control).
KEY_SHORTCUT = "Ctrl+,"
KEY_SHORTCUT_LABEL = "⌘," if platform.system() == "Darwin" else "ctrl+,"


def _screen_at(point) -> object:
    """The screen containing a global point, or the primary one."""
    return QApplication.screenAt(point) or QApplication.primaryScreen()


def _join_active_space(widget) -> None:
    """Let the window appear on whichever macOS Space is active.

    Without this, macOS switches Spaces to wherever the window was first
    shown. Qt has no flag for it, so set it on the underlying NSWindow.
    """
    if platform.system() != "Darwin":
        return
    try:
        import objc
        from AppKit import NSWindowCollectionBehaviorCanJoinAllSpaces

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        # CanJoinAllSpaces can't be OR'd with MoveToActiveSpace (AppKit raises);
        # on its own it puts the window on whichever Space is active.
        window.setCollectionBehavior_(
            window.collectionBehavior() | NSWindowCollectionBehaviorCanJoinAllSpaces
        )
    except Exception:  # noqa: BLE001 - cosmetic: window still works
        pass


def _log_permissions() -> None:
    """Record this process's TCC grants to ~/.imit8/permissions.json.

    The bundle and a CLI-launched python are different TCC clients, so the app
    must self-report — diagnostics for exactly this setup step.
    """
    if platform.system() != "Darwin":
        return
    try:
        import ctypes
        import json

        from Quartz import (  # noqa: PLC0415 - macOS-only
            CGPreflightListenEventAccess,
            CGPreflightScreenCaptureAccess,
        )

        lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        state = {
            "accessibility": bool(lib.AXIsProcessTrusted()),
            "input_monitoring": bool(CGPreflightListenEventAccess()),
            "screen_recording": bool(CGPreflightScreenCaptureAccess()),
        }
        (HOME_DIR / "permissions.json").write_text(json.dumps(state))
    except Exception:  # noqa: BLE001 - diagnostics only
        pass


def _apply_vibrancy(widget, radius: float = 20.0, margin: float = 12.0) -> bool:
    """Back a translucent Qt window with real macOS vibrancy.

    HIG materials: a floating functional panel should be a system material,
    not a flat fill — NSVisualEffectView adapts to the desktop picture,
    appearance, and accessibility settings for free. A rounded maskImage
    matches the card's corners. Returns False off-macOS or without pyobjc,
    where the stylesheet's solid fallback keeps the old look.
    """
    if platform.system() != "Darwin":
        return False
    try:
        import objc
        from AppKit import (  # noqa: PLC0415
            NSBezierPath,
            NSColor,
            NSImage,
            NSMakeRect,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSWindowBelow,
        )

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        if window is None:
            return False
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(True)
        content = window.contentView()
        vev = getattr(widget, "_vev", None)
        if vev is None:
            vev = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
            vev.setMaterial_(NSVisualEffectMaterialHUDWindow)
            vev.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            vev.setState_(NSVisualEffectStateActive)
            vev.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            content.addSubview_positioned_relativeTo_(vev, NSWindowBelow, None)
            widget._vev = vev
        w, h = widget.width(), widget.height()
        mask = NSImage.alloc().initWithSize_((w, h))
        mask.lockFocus()
        NSColor.blackColor().set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(margin, margin, w - 2 * margin, h - 2 * margin), radius, radius
        ).fill()
        mask.unlockFocus()
        vev.setMaskImage_(mask)
        return True
    except Exception:  # noqa: BLE001 - solid fallback styling still applies
        return False


def _reduce_motion() -> bool:
    """The system Reduce Motion setting — animations become optional, not assumed."""
    if platform.system() != "Darwin":
        return False
    try:
        from AppKit import NSWorkspace  # noqa: PLC0415

        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:  # noqa: BLE001
        return False


def _activate_app() -> None:
    """Make this process the active app so the window takes keyboard focus.

    An LSUIElement agent can't pull focus with raise()/activateWindow() alone —
    without this the spotlight opens behind the current app on macOS.
    """
    if platform.system() != "Darwin":
        return
    try:
        from AppKit import (  # noqa: PLC0415 - macOS-only
            NSApp,
            NSApplicationActivateIgnoringOtherApps,
        )

        if hasattr(NSApp, "activateWithOptions_"):
            # macOS 14+: activateIgnoringOtherApps: is deprecated and ignored.
            NSApp.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        else:
            NSApp.activateIgnoringOtherApps_(True)
    except Exception:  # noqa: BLE001
        pass


def _stamp_trigger() -> None:
    """Diagnostics: prove a global hotkey event reached the app."""
    try:
        (HOME_DIR / "last_trigger").write_text(str(time.time()))
    except Exception:  # noqa: BLE001
        pass


def _ensure_accessibility() -> None:
    """Pop the system prompt when the global hotkey can't attach (macOS only).

    A CGEventTap listener needs the process in the Accessibility clients list;
    without it macOS just swallows keystrokes, so ask explicitly at launch.
    """
    if platform.system() != "Darwin":
        return
    try:
        from ApplicationServices import (  # noqa: PLC0415 - macOS-only
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        if not AXIsProcessTrusted():
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:  # noqa: BLE001 - prompting is best-effort
        pass


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#14151a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#7a8cff"))
    painter.setFont(QFont("Sans", 26, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "i8")
    painter.end()
    return QIcon(pixmap)


class Hotkey(QObject):
    """Global shortcuts that summon the spotlight from anywhere."""

    triggered = Signal()  # each bare ctrl+option tap
    combo = Signal()  # the full ctrl+option+space chord

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 - optional dependency / no input perms
            return False
        try:
            self._hotkey = keyboard.HotKey(keyboard.HotKey.parse(HOTKEY), self.combo.emit)
            self._chord = ModifierChord([keyboard.Key.ctrl, keyboard.Key.alt], self.triggered.emit)
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.daemon = True
            self._listener.start()
        except Exception:  # noqa: BLE001 - e.g. missing accessibility permission
            return False
        return True

    def _on_press(self, key, injected: bool = False) -> None:
        if not injected:
            canonical = self._listener.canonical(key)
            self._hotkey.press(canonical)
            self._chord.press(canonical)

    def _on_release(self, key, injected: bool = False) -> None:
        if not injected:
            canonical = self._listener.canonical(key)
            self._hotkey.release(canonical)
            self._chord.release(canonical)


class TapRouter(QObject):
    """Counts bare ctrl+option taps: a single tap toggles, a double tap scopes."""

    single = Signal()
    double = Signal()

    def __init__(self, interval_ms: int = 480) -> None:
        super().__init__()
        self._taps = 0
        self._interval = interval_ms
        self._timer = QTimer(self, singleShot=True, timeout=self._flush)

    def tap(self) -> None:
        self._taps += 1
        self._timer.start(self._interval)

    def _flush(self) -> None:
        (self.double if self._taps > 1 else self.single).emit()
        self._taps = 0


class SchedulerBridge(QObject):
    """Hops a due schedule from the poller thread onto the Qt event loop."""

    due = Signal(object)


class Runner(QThread):
    event = Signal(object)
    finished_run = Signal(object)

    def __init__(
        self,
        agent: Agent,
        task: str = "",
        flow: Flow | None = None,
        scope: ScopedWindow | None = None,
        fast: bool = False,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.task = task
        self.flow = flow
        self.scope = scope
        self.fast = fast

    def run(self) -> None:  # noqa: D102
        sink: Callable[[Event], None] = self.event.emit
        previous_scope = self.agent.computer.scope
        try:
            if self.flow is not None:
                result = self.agent.replay(self.flow, sink)
            elif self.fast:
                result = self.agent.run_fast(self.task, sink)
            else:
                if self.scope is not None:
                    self.scope.focus()
                    self.agent.computer.scope = self.scope
                result = self.agent.run(self.task, sink, context=self._context())
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.event.emit(Event("error", str(exc)))
            result = Result("error", str(exc), 0, 0.0, [])
        finally:
            self.agent.computer.scope = previous_scope
        self.finished_run.emit(result)

    def _context(self) -> str:
        if self.scope is None:
            return ""
        return (
            f"Work only inside the {self.scope.app} window. Every screenshot shows just "
            "that window — its top-left corner is (0,0) — and clicks, scrolls and drags "
            "are confined to it. Do not try to leave it or switch apps."
        )


class ScopeOverlay(QWidget):
    """Blurred screen backdrop with a clear hole over the scoped window.

    Click-through and non-activating: it only *shows* the scope while the
    spotlight is up; the real window stays visible and usable through the hole.
    """

    def __init__(self, hole: QRect, blur: QPixmap | None) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if platform.system() == "Darwin":
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self._hole = hole
        self._blur = blur
        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)

    def set_hole(self, hole: QRect) -> None:
        self._hole = hole
        self.update()

    def paintEvent(self, _) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        origin = self.geometry().topLeft()
        hole = self._hole.translated(-origin)
        painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(hole)))
        for i, screen in enumerate(QApplication.screens()):
            rect = screen.geometry().translated(-origin)
            if i == 0 and self._blur is not None:
                painter.drawPixmap(rect, self._blur)
            painter.fillRect(rect, QColor(12, 14, 22, 150))
        painter.end()


class ActivityOrb(QWidget):
    """Floating ring that tracks a run in flight; click it to peek at details."""

    clicked = Signal()

    # macOS system palette (dark variants); "running" resolves to the user's accent
    COLORS = {
        "running": None,
        "success": "#30d158",
        "failed": "#ff453a",
        "error": "#ff453a",
        "stopped": "#ff9f0a",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if platform.system() == "Darwin":
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setFixedSize(320, 52)
        self._angle = 0
        self._state = "running"
        self._steps = 0
        self._text = ""
        self._icon: QPixmap | None = None
        self._glass = False
        self._spin = QTimer(self, interval=40, timeout=self._tick)

    def begin(self, text: str, icon: QPixmap | None = None, anchor=None) -> None:
        self._state, self._steps, self._text = "running", 0, text
        self._icon = icon
        screen = _screen_at(anchor if anchor is not None else QCursor.pos()).geometry()
        self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 56)
        self.show()
        _join_active_space(self)
        self._glass = _apply_vibrancy(self, radius=26, margin=0)
        # reduce motion: draw the full ring instead of spinning
        if not _reduce_motion():
            self._spin.start()

    def update_event(self, event: Event) -> None:
        if event.kind == "step" and "step" in event.data:
            self._steps = event.data["step"]
        if event.kind in {"action", "step", "error", "perf"}:
            self._text = event.text
        self.update()

    def finish(self, result: Result) -> None:
        self._state = result.status
        self._text = result.summary or result.status
        self._spin.stop()
        self.update()
        QTimer.singleShot(2400, self.hide)

    def _tick(self) -> None:
        self._angle = (self._angle + 14) % 360
        self.update()

    def mousePressEvent(self, _) -> None:  # noqa: N802 - Qt override
        self.clicked.emit()

    def paintEvent(self, _) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 22, 30, 70 if self._glass else 225))
        painter.drawRoundedRect(self.rect(), 26, 26)

        ring = QRect(12, 10, 32, 32)
        color = QColor(self.COLORS.get(self._state) or _accent())
        painter.setPen(QPen(QColor(255, 255, 255, 40), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(ring)
        painter.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap))
        if self._state == "running" and self._spin.isActive():
            painter.drawArc(ring, self._angle * 16, 100 * 16)
        else:
            painter.drawEllipse(ring)
        painter.setPen(QColor("#f5f6fa"))
        painter.setFont(QFont("Sans", 11, QFont.Bold))
        painter.drawText(ring, Qt.AlignCenter, str(self._steps) if self._steps else "·")

        text_x = 54
        if self._icon is not None:
            painter.drawPixmap(QRect(52, 14, 24, 24), self._icon)
            text_x = 84
        text_rect = QRect(text_x, 0, self.width() - text_x - 10, self.height())
        elided = painter.fontMetrics().elidedText(self._text, Qt.ElideRight, text_rect.width())
        painter.setPen(QColor("#c8cddb"))
        painter.setFont(QFont("Sans", 12))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)
        painter.end()


class Spotlight(QWidget):
    def __init__(self, agent: Agent | None = None) -> None:
        super().__init__()
        self.agent = agent or Agent()
        self.runner: Runner | None = None
        self.last_error = ""
        self.schedules = ScheduleStore()
        self.calendar: CalendarWindow | None = None
        self.active_schedule: Schedule | None = None
        self._entering_key = False
        self.scope: ScopedWindow | None = None
        self.overlay: ScopeOverlay | None = None
        self._scope_pix: QPixmap | None = None
        self._icon_cache: dict[str, QPixmap] = {}
        self.orb = ActivityOrb()
        self.orb.clicked.connect(self.show_spotlight)
        self._build()
        self.refresh_flows()

        self.bridge = SchedulerBridge()
        self.bridge.due.connect(self.run_scheduled)
        self.scheduler = Scheduler(self.bridge.due.emit, store=self.schedules)
        self.scheduler.start()

    # --- layout ------------------------------------------------------------
    def _build(self) -> None:
        self.setWindowTitle("imit8")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._glass = False
        self._chip_buttons: list[QPushButton] = []
        self.setStyleSheet(_style())
        self.setMinimumWidth(680)
        self.resize(680, 200)

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
        self.prompt.returnPressed.connect(self.on_return)
        layout.addWidget(self.prompt)

        self.key_hint = QLabel(f"no openrouter key — {KEY_SHORTCUT_LABEL} to set one")
        self.key_hint.setObjectName("status")
        self.key_hint.setVisible(not self.agent.config.api_key)
        layout.addWidget(self.key_hint)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.scope_icon = QLabel()
        self.scope_icon.setFixedSize(18, 18)
        self.scope_icon.hide()
        status_row.addWidget(self.scope_icon)
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.hide()
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.chips = QVBoxLayout()
        self.chips.setSpacing(6)
        layout.addLayout(self.chips)

        self.upcoming = QLabel("")
        self.upcoming.setObjectName("upcoming")
        self.upcoming.setWordWrap(True)
        self.upcoming.hide()
        layout.addWidget(self.upcoming)

        footer = QHBoxLayout()
        hint = QLabel(
            f"⏎ run  ·  esc hide  ·  {HOTKEY_LABEL} anywhere  ·  ↓ pick a flow"
        )
        hint.setObjectName("hint")
        footer.addWidget(hint)
        footer.addStretch(1)
        self.calendar_button = QPushButton("Schedule…")
        self.calendar_button.setObjectName("ghost")
        self.calendar_button.setCursor(Qt.PointingHandCursor)
        self.calendar_button.clicked.connect(self.open_calendar)
        footer.addWidget(self.calendar_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop_run)
        self.stop_button.hide()
        footer.addWidget(self.stop_button)
        layout.addLayout(footer)

        self.prompt.installEventFilter(self)

        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        QShortcut(QKeySequence(KEY_SHORTCUT), self, activated=self.begin_key_entry)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        """↓ from the prompt hands keyboard focus to the first flow chip."""
        if (
            obj is self.prompt
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Down
            and self._chip_buttons
        ):
            self._chip_buttons[0].setFocus()
            return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._glass:
            _apply_vibrancy(self)  # keep the rounded material mask in sync

    def refresh_flows(self) -> None:
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chip_buttons = []
        for flow in self.agent.store.suggestions():
            bolt = "↻ " if flow.replayable else ""
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            button = QPushButton(f"{bolt}{flow.task}     · {flow.badge()}, run it again?")
            button.setObjectName("chip")
            icon = self._flow_icon(flow)
            if icon is not None:
                button.setIcon(QIcon(icon))
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.StrongFocus)  # keyboard navigable, not just mouse
            button.setAccessibleName(f"Run flow: {flow.task}")
            self._chip_buttons.append(button)
            button.clicked.connect(lambda _=False, f=flow: self.start_flow(f))
            row_layout.addWidget(button, 1)
            forget = QPushButton("×")
            forget.setObjectName("ghost")
            forget.setFixedWidth(34)
            forget.setCursor(Qt.PointingHandCursor)
            forget.setToolTip("forget this flow")
            forget.setAccessibleName(f"Forget flow {flow.task}")  # icon-only controls need a name
            forget.clicked.connect(lambda _=False, f=flow: self.forget_flow(f))
            row_layout.addWidget(forget)
            self.chips.addWidget(row)
        self.refresh_upcoming()
        self.resize(self.width(), self.sizeHint().height())

    def forget_flow(self, flow: Flow) -> None:
        self.agent.store.forget(flow.id)
        self.refresh_flows()

    def _flow_icon(self, flow: Flow) -> QPixmap | None:
        """The app icon for flows recorded inside a window scope."""
        trace = flow.trace or []
        if trace and trace[0].get("name") == "_scope":
            app = trace[0].get("arguments", {}).get("app")
            if app:
                return self._app_icon(name=app)
        return None

    def refresh_upcoming(self) -> None:
        lines = []
        for schedule in self.schedules.upcoming(2):
            when = schedule.next_run()
            if when is None:
                continue
            stamp = datetime.fromtimestamp(when).strftime("%a %H:%M")
            lead = "next" if not lines else "then"
            lines.append(f"{lead} · {schedule.task} · {stamp}")
        self.upcoming.setText("\n".join(lines))
        self.upcoming.setVisible(bool(lines))

    def open_calendar(self) -> None:
        if self.calendar is None:
            self.calendar = CalendarWindow(store=self.schedules, flows=self.agent.store)
            self.calendar.saved.connect(self.refresh_upcoming)
        self.calendar.refresh()
        self.hide()  # the spotlight is always-on-top and would sit over the grid
        self.calendar.show()
        self.calendar.raise_()
        self.calendar.activateWindow()

    # --- api key entry -------------------------------------------------------
    def begin_key_entry(self) -> None:
        """Swap the prompt into masked key-entry mode (⌘, / ctrl+,)."""
        if self.runner and self.runner.isRunning():
            return
        self._entering_key = True
        self.prompt.clear()
        self.prompt.setEchoMode(QLineEdit.Password)
        self.prompt.setPlaceholderText("openrouter api key — enter to save, esc to cancel")
        self.show_spotlight()

    def save_key(self) -> None:
        key = self.prompt.text().strip()
        self._end_key_entry()
        if not key:
            return
        self.agent.config.api_key = key  # the client holds this same Config object
        self.agent.config.save_api_key()
        self.status.setText("api key saved")
        self.status.show()
        self.key_hint.hide()

    def _end_key_entry(self) -> None:
        self._entering_key = False
        self.prompt.setEchoMode(QLineEdit.Normal)
        self.prompt.setPlaceholderText("wyd?")
        self.prompt.clear()

    def _escape(self) -> None:
        if self._entering_key:
            self._end_key_entry()
        elif self.scope is not None:
            self.clear_scope()
            self.hide()
        else:
            self.hide()

    # --- window scoping ------------------------------------------------------
    def begin_scoped(self) -> None:
        """Double-tap: bind the spotlight to the frontmost app window."""
        if self.runner and self.runner.isRunning():
            return
        window = frontmost_window(exclude_pid=os.getpid())
        if window is None:
            self.status.setText("no window to scope to — needs macOS")
            self.status.show()
            self.show_spotlight()
            return
        self.scope = window
        if self.overlay is not None:
            self.overlay.close()
        self.overlay = ScopeOverlay(self._hole(), self._blur_pixmap())
        self.overlay.show()
        _join_active_space(self.overlay)
        self._scope_pix = self._app_icon(pid=window.pid)
        if self._scope_pix is not None:
            self.scope_icon.setPixmap(
                self._scope_pix.scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.scope_icon.show()
        self.prompt.setPlaceholderText(f"in {window.app} — wyd?")
        self.status.setText(f"scoped to {window.app} — esc clears")
        self.status.show()
        self.show_spotlight()

    def clear_scope(self) -> None:  # noqa: D102
        self.scope = None
        self._scope_pix = None
        self.scope_icon.hide()
        if self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        self.prompt.setPlaceholderText("wyd?")
        if not (self.runner and self.runner.isRunning()):
            self.status.hide()

    def _app_icon(self, pid: int = 0, name: str = "") -> QPixmap | None:
        """App icon pixmap, cached per app so the list doesn't re-query."""
        key = str(pid or name)
        if key in self._icon_cache:
            return self._icon_cache[key]
        data = app_icon_png(pid=pid, name=name)
        pixmap = QPixmap()
        if data is None or not pixmap.loadFromData(data):
            return None
        self._icon_cache[key] = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return self._icon_cache[key]

    def _hole(self) -> QRect:
        return QRect(*self.scope.bounds()).adjusted(-6, -6, 6, 6)

    def _blur_pixmap(self) -> QPixmap | None:
        """The screen, blurred — everything but the scoped window goes soft."""
        try:
            from PIL import ImageFilter

            image = self.agent.computer._grab()
            image = image.resize((max(1, image.width // 4), max(1, image.height // 4)))
            image = image.filter(ImageFilter.GaussianBlur(10))
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())
            return pixmap
        except Exception:  # noqa: BLE001 - no screen permission yet: just dim
            return None

    # --- running -----------------------------------------------------------
    def on_return(self) -> None:
        if self._entering_key:
            self.save_key()
        else:
            self.start_task()

    def start_task(self) -> None:
        task = self.prompt.text().strip()
        if task:
            fast = task.startswith("fast ")
            runner = Runner(
                self.agent,
                task=task.removeprefix("fast "),
                scope=self.scope,
                fast=fast,
            )
            self._start(runner, f"{'fast ' if fast else ''}running: {task}")

    def start_flow(self, flow: Flow) -> None:
        runner = Runner(
            self.agent,
            task=flow.task,
            flow=flow if flow.replayable else None,
            scope=self.scope,
        )
        verb = "replaying" if flow.replayable else "running"
        self._start(runner, f"{verb}: {flow.task}")

    def run_scheduled(self, schedule: Schedule) -> None:
        """Fire a scheduled task: replay its trace when it has one."""
        if self.runner and self.runner.isRunning():
            self.schedules.mark_run(schedule.id, "skipped (busy)")
            return
        flow = self.agent.store.find(schedule.task)
        replay = flow if (flow and flow.replayable) else None
        self.active_schedule = schedule
        self._start(
            Runner(self.agent, task=schedule.task, flow=replay),
            f"scheduled: {schedule.task}",
        )

    def _start(self, runner: Runner, label: str) -> None:
        if self.runner and self.runner.isRunning():
            return
        self.prompt.clear()
        self.prompt.setEnabled(False)
        self.last_error = ""
        self.status.setText(label)
        self.status.show()
        self.stop_button.show()
        self.runner = runner
        runner.event.connect(self.on_event)
        runner.finished_run.connect(self.on_finished)
        if self.overlay is not None:
            self.overlay.hide()
        icon = self._scope_pix if runner.scope is not None else None
        anchor = self._hole().center() if runner.scope is not None else QCursor.pos()
        self.orb.begin(label, icon, anchor)
        self.showMinimized()  # get out of the agent's way
        runner.start()

    def stop_run(self) -> None:
        self.agent.stop()
        self.status.setText("stopping…")

    def on_event(self, event: Event) -> None:
        if event.kind == "error":
            self.last_error = event.text
        if event.kind in {"action", "step", "error", "done", "flow", "perf"}:
            self.status.setText(event.text)
        self.orb.update_event(event)

    def on_finished(self, result: Result) -> None:
        self.prompt.setEnabled(True)
        self.stop_button.hide()
        line = f"{result.status} · {result.steps} steps · {result.duration:.1f}s"
        detail = result.summary or self.last_error
        if result.status in {"error", "failed"} and detail:
            line = f"{line} — {detail}"
        self.status.setText(line)
        self.status.setToolTip(detail)
        if self.active_schedule is not None:
            self.schedules.mark_run(self.active_schedule.id, result.status)
            self.active_schedule = None
        self.orb.finish(result)
        self.refresh_flows()
        self.show_spotlight()

    def show_spotlight(self) -> None:
        if self.scope is not None and self.overlay is not None:
            self.overlay.set_hole(self._hole())
            self.overlay.show()
            _join_active_space(self.overlay)
        screen = _screen_at(QCursor.pos()).geometry()
        self.move(screen.center().x() - self.width() // 2, int(screen.y() + screen.height() * 0.22))
        self.showNormal()
        if not self._glass and _apply_vibrancy(self):
            self._glass = True
            self.setStyleSheet(_style(glass=True))  # let the material show through
        _join_active_space(self)
        self.raise_()
        self.activateWindow()
        _activate_app()
        self.prompt.setFocus()

    def toggle_spotlight(self) -> None:
        self.hide() if self.isVisible() else self.show_spotlight()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("imit8")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    window = Spotlight()

    tray = QSystemTrayIcon(app_icon(), app)
    tray.setToolTip("imit8 — wyd?")
    menu = QMenu()
    open_action = QAction(f"Wyd?  ({HOTKEY_LABEL})", menu)
    open_action.triggered.connect(window.show_spotlight)
    schedule_action = QAction("Schedule…", menu)
    schedule_action.triggered.connect(window.open_calendar)
    key_action = QAction(f"Set API Key…  ({KEY_SHORTCUT_LABEL})", menu)
    key_action.triggered.connect(window.begin_key_entry)
    quit_action = QAction("Quit imit8", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(open_action)
    menu.addAction(schedule_action)
    menu.addAction(key_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show_spotlight() if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    router = TapRouter()
    router.single.connect(window.toggle_spotlight)
    router.double.connect(window.begin_scoped)

    hotkey = Hotkey()
    hotkey.triggered.connect(router.tap)
    hotkey.triggered.connect(_stamp_trigger)
    hotkey.combo.connect(window.toggle_spotlight)
    hotkey.start()
    _log_permissions()
    _ensure_accessibility()

    window.show_spotlight()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
