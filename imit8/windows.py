"""Find and track the user's frontmost window so a run can be scoped to it.

macOS only, via Quartz's window list — no accessibility permission needed to
read bounds, owner and z-order (titles need Screen Recording). Other platforms
return nothing and the UI falls back to an unscoped prompt.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass

# These own layer-0 windows that cover the desktop but aren't "apps".
_IGNORED_OWNERS = {"Dock", "Window Server", "SystemUIServer", "ControlCenter", "loginwindow"}
_MIN_SIZE = (120, 80)


@dataclass
class ScopedWindow:
    app: str
    pid: int
    window_id: int
    title: str
    rect: tuple[int, int, int, int]  # x, y, w, h in logical screen coords

    def bounds(self) -> tuple[int, int, int, int]:
        """Live rect — windows move and resize, so re-query every screenshot."""
        try:
            from Quartz import (  # noqa: PLC0415 - macOS-only import
                CGWindowListCopyWindowInfo,
                kCGWindowListOptionIncludingWindow,
            )

            infos = CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, self.window_id)
            if infos:
                self.rect = _rect(infos[0])
        except Exception:  # noqa: BLE001 - keep last known bounds
            pass
        return self.rect

    def focus(self) -> None:
        """Raise the owning app so clicks land on it. Best-effort."""
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to set frontmost of '
                    f"(first process whose unix id is {self.pid}) to true",
                ],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except Exception:  # noqa: BLE001 - scoping still works unfocused
            pass

    def icon_png(self) -> bytes | None:
        """The owning app's icon, as PNG bytes."""
        return app_icon_png(pid=self.pid)


def app_icon_png(pid: int = 0, name: str = "") -> bytes | None:
    """PNG bytes of an app's icon — by running pid, or by app name."""
    if platform.system() != "Darwin":
        return None
    try:
        from AppKit import (  # noqa: PLC0415 - macOS-only import
            NSBitmapImageRep,
            NSPNGFileType,
            NSRunningApplication,
            NSWorkspace,
        )

        image = None
        if pid:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            image = None if app is None else app.icon()
        if image is None and name:
            path = NSWorkspace.sharedWorkspace().fullPathForApplication_(name)
            if path:
                image = NSWorkspace.sharedWorkspace().iconForFile_(path)
        if image is None:
            return None
        rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data = rep.representationUsingType_properties_(NSPNGFileType, None)
        return None if data is None else bytes(data)
    except Exception:  # noqa: BLE001 - no AppKit / no icon
        return None


def _rect(info: dict) -> tuple[int, int, int, int]:
    b = info.get("kCGWindowBounds") or {}
    return (
        int(b.get("X", 0)),
        int(b.get("Y", 0)),
        int(b.get("Width", 0)),
        int(b.get("Height", 0)),
    )


def _pick(infos: list[dict], exclude_pid: int) -> ScopedWindow | None:
    """First front-to-back window that isn't ours, a system shell, or tiny."""
    for info in infos:
        if info.get("kCGWindowLayer", 0) != 0:
            continue
        pid = int(info.get("kCGWindowOwnerPID", -1))
        if pid == exclude_pid:
            continue
        owner = info.get("kCGWindowOwnerName") or ""
        if owner in _IGNORED_OWNERS:
            continue
        rect = _rect(info)
        if rect[2] < _MIN_SIZE[0] or rect[3] < _MIN_SIZE[1]:
            continue
        return ScopedWindow(
            app=owner,
            pid=pid,
            window_id=int(info.get("kCGWindowNumber", 0)),
            title=info.get("kCGWindowName") or "",
            rect=rect,
        )
    return None


def frontmost_window(exclude_pid: int = 0) -> ScopedWindow | None:
    """The frontmost real app window, skipping our own process."""
    if platform.system() != "Darwin":
        return None
    try:
        from Quartz import (  # noqa: PLC0415 - macOS-only import
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )

        infos = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        return _pick(list(infos or []), exclude_pid)
    except Exception:  # noqa: BLE001 - no Quartz / no permission
        return None
