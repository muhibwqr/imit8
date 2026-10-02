"""Enumerate the frontmost app's interactive elements via the Accessibility API.

This is the local detector in the fast loop: macOS already knows every button,
field and link on screen — role, label and frame — so there is nothing to
segment or OCR. A full dump costs ~10-30ms. macOS only; needs Accessibility.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field

# Roles worth offering to the decision model — clickable or typeable things.
ACTIONABLE = {
    "AXButton",
    "AXLink",
    "AXTextField",
    "AXTextArea",
    "AXCheckBox",
    "AXRadioButton",
    "AXMenuItem",
    "AXMenuButton",
    "AXPopUpButton",
    "AXComboBox",
    "AXSlider",
    "AXTab",
    "AXCell",
    "AXRow",
    "AXStaticText",  # page text doubles as the "OCR" half of the detector
    "AXImage",
    "AXGroup",  # web UIs expose clickable regions as groups
}
TYPEABLE = {"AXTextField", "AXTextArea", "AXComboBox"}

MAX_ELEMENTS = 40
_MAX_DEPTH = 12
_MAX_VISITED = 600  # each attribute read is an IPC call — bound Electron-size trees
_LABEL_MAX = 60


@dataclass
class Element:
    index: int
    role: str
    label: str
    x: int  # center, in logical screen points — ready for pyautogui
    y: int
    _ref: object = field(default=None, repr=False, compare=False)

    def line(self) -> str:
        return f"[{self.index}] {self.role.removeprefix('AX')} {self.label!r} @({self.x},{self.y})"

    def press(self) -> bool:
        """AXPress the element directly — the cursor never moves, and it works
        even when the window is covered. False when there's no live ref."""
        if self._ref is None:
            return False
        try:
            from ApplicationServices import (  # noqa: PLC0415 - macOS-only
                AXUIElementPerformAction,
                kAXPressAction,
            )

            return AXUIElementPerformAction(self._ref, kAXPressAction) == 0
        except Exception:  # noqa: BLE001 - dead element / no AX: caller falls back to a click
            return False


def frontmost_elements(limit: int = MAX_ELEMENTS) -> tuple[str, list[Element]]:
    """(app name, elements) for the frontmost real app window; ("",[]) off-macOS."""
    if platform.system() != "Darwin":
        return "", []
    try:
        from ApplicationServices import (  # noqa: PLC0415 - macOS-only
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
        )
        from Quartz import (  # noqa: PLC0415 - macOS-only
            AXValueGetValue,
            kAXValueCGPointType,
            kAXValueCGSizeType,
        )

        from .windows import frontmost_window

        def attr(el, name):
            try:
                err, val = AXUIElementCopyAttributeValue(el, name, None)
                return None if err else val
            except Exception:  # noqa: BLE001 - dead element mid-walk
                return None

        def point_of(el):
            pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
            if pos is None or size is None:
                return None
            ok_p, p = AXValueGetValue(pos, kAXValueCGPointType, None)
            ok_s, s = AXValueGetValue(size, kAXValueCGSizeType, None)
            if not (ok_p and ok_s):
                return None
            px, py = (p.x, p.y) if hasattr(p, "x") else (p["x"], p["y"])
            sw, sh = (s.width, s.height) if hasattr(s, "width") else (s["width"], s["height"])
            return int(px + sw / 2), int(py + sh / 2)

        window = frontmost_window(exclude_pid=os.getpid())
        if window is None:
            return "", []
        root = AXUIElementCreateApplication(window.pid)
        elements: list[Element] = []
        visited = [0]

        def walk(el, depth: int) -> None:
            if depth > _MAX_DEPTH or len(elements) >= limit or visited[0] >= _MAX_VISITED:
                return
            visited[0] += 1
            role = attr(el, "AXRole")
            if role in ACTIONABLE:
                label = ""
                for key in ("AXTitle", "AXDescription", "AXValue", "AXRoleDescription"):
                    val = attr(el, key)
                    if isinstance(val, str) and val.strip():
                        label = val.strip()[:_LABEL_MAX]
                        break
                center = point_of(el)
                if center is not None:
                    elements.append(Element(len(elements), role, label, *center, _ref=el))
            for child in attr(el, "AXChildren") or []:
                walk(child, depth + 1)

        walk(root, 0)
        return window.app, elements
    except Exception:  # noqa: BLE001 - no AX permission / no Quartz
        return "", []
