"""Cross-platform screen capture and input control.

Coordinates used by the model are always in the *scaled* screenshot space; the
controller maps them back to physical pixels so the model never has to reason
about retina scaling or odd resolutions.
"""

from __future__ import annotations

import base64
import io
import platform
import time
from dataclasses import dataclass


@dataclass
class Screenshot:
    png: bytes
    width: int
    height: int

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode()


class Computer:
    def __init__(self, target_width: int = 1280, action_delay: float = 0.15) -> None:
        self.target_width = target_width
        self.action_delay = action_delay
        self._scale = 1.0
        self._pyautogui = None
        self._sct = None

    @property
    def pyautogui(self):
        if self._pyautogui is None:
            import pyautogui

            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0
            self._pyautogui = pyautogui
        return self._pyautogui

    def screen_size(self) -> tuple[int, int]:
        return tuple(self.pyautogui.size())  # type: ignore[return-value]

    def _grab(self):
        """Capture the primary monitor.

        mss is the fast path; some X servers reject its shared-memory capture, so
        fall back to Pillow's grab.
        """
        from PIL import Image, ImageGrab

        if self._sct is not False:
            try:
                import mss

                if self._sct is None:
                    self._sct = mss.mss()
                raw = self._sct.grab(self._sct.monitors[1])
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:  # noqa: BLE001 - fall back to Pillow
                self._sct = False
        return ImageGrab.grab().convert("RGB")

    def screenshot(self) -> Screenshot:
        from PIL import Image

        raw = self._grab()
        self._scale = self.target_width / raw.width if raw.width > self.target_width else 1.0
        if self._scale != 1.0:
            size = (int(raw.width * self._scale), int(raw.height * self._scale))
            raw = raw.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        raw.save(buf, format="PNG")
        return Screenshot(png=buf.getvalue(), width=raw.width, height=raw.height)

    def _to_physical(self, x: float, y: float) -> tuple[int, int]:
        if self._scale in (0, 1.0):
            return int(x), int(y)
        return int(x / self._scale), int(y / self._scale)

    # --- actions -----------------------------------------------------------
    def move(self, x: float, y: float) -> None:
        px, py = self._to_physical(x, y)
        self.pyautogui.moveTo(px, py, duration=0.1)

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        px, py = self._to_physical(x, y)
        self.pyautogui.click(px, py, button=button, clicks=clicks, interval=0.05)
        time.sleep(self.action_delay)

    def double_click(self, x: float, y: float) -> None:
        self.click(x, y, clicks=2)

    def right_click(self, x: float, y: float) -> None:
        self.click(x, y, button="right")

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        sx, sy = self._to_physical(x1, y1)
        ex, ey = self._to_physical(x2, y2)
        self.pyautogui.moveTo(sx, sy)
        self.pyautogui.dragTo(ex, ey, duration=0.3, button="left")
        time.sleep(self.action_delay)

    def type_text(self, text: str) -> None:
        self.pyautogui.write(text, interval=0.01)
        time.sleep(self.action_delay)

    def key(self, keys: str) -> None:
        parts = [p.strip().lower() for p in keys.replace("-", "+").split("+") if p.strip()]
        parts = [self._normalize_key(p) for p in parts]
        if len(parts) == 1:
            self.pyautogui.press(parts[0])
        else:
            self.pyautogui.hotkey(*parts)
        time.sleep(self.action_delay)

    def scroll(self, x: float, y: float, amount: int) -> None:
        px, py = self._to_physical(x, y)
        self.pyautogui.scroll(amount, x=px, y=py)
        time.sleep(self.action_delay)

    @staticmethod
    def _normalize_key(key: str) -> str:
        aliases = {
            "cmd": "command",
            "super": "win" if platform.system() == "Windows" else "command",
            "meta": "command",
            "control": "ctrl",
            "return": "enter",
            "esc": "escape",
        }
        key = aliases.get(key, key)
        if key == "command" and platform.system() != "Darwin":
            return "ctrl"
        return key
