"""Cross-platform screen capture and input control.

Coordinates used by the model are always in the *scaled* screenshot space; the
controller maps them back to physical pixels so the model never has to reason
about retina scaling or odd resolutions.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import subprocess
import time
from dataclasses import dataclass


@dataclass
class Screenshot:
    image: bytes  # JPEG — ~5-10x smaller than PNG on the wire, so the model ingests sooner
    width: int
    height: int

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.image).decode()


@dataclass
class ScopeRegion:
    """A frozen rectangle scope — what replays of recorded flows use."""

    rect: tuple[int, int, int, int]
    app: str = ""

    def bounds(self) -> tuple[int, int, int, int]:
        return self.rect


class Computer:
    def __init__(self, target_width: int = 1280, action_delay: float = 0.15) -> None:
        self.target_width = target_width
        self.action_delay = action_delay
        self.scope = None  # anything with .bounds() -> (x, y, w, h) in logical coords
        self._scale = 1.0
        self._backing = 1.0  # raw capture pixels per logical point (2.0 on retina)
        self._crop_origin = (0, 0)  # where the sent screenshot starts, in raw pixels
        self._region: tuple[int, int, int, int] | None = None  # logical, for clamping
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
        try:
            logical_w = self.pyautogui.size()[0]
        except Exception:  # noqa: BLE001 - assume 1:1 mapping
            logical_w = raw.width
        self._backing = raw.width / logical_w if logical_w else 1.0
        self._crop_origin = (0, 0)
        self._region = None
        if self.scope is not None:
            raw = self._crop(raw)
        self._scale = self.target_width / raw.width if raw.width > self.target_width else 1.0
        if self._scale != 1.0:
            size = (int(raw.width * self._scale), int(raw.height * self._scale))
            raw = raw.resize(size, Image.BILINEAR)
        buf = io.BytesIO()
        raw.save(buf, format="JPEG", quality=85)
        return Screenshot(image=buf.getvalue(), width=raw.width, height=raw.height)

    def _crop(self, raw):
        """Crop the capture to the scoped window, in raw pixel space."""
        try:
            x, y, w, h = self.scope.bounds()
        except Exception:  # noqa: BLE001 - scope vanished, go unscoped
            return raw
        b = self._backing
        box = (int(x * b), int(y * b), int((x + w) * b), int((y + h) * b))
        box = (
            max(0, box[0]),
            max(0, box[1]),
            min(raw.width, box[2]),
            min(raw.height, box[3]),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return raw  # window off the captured display — don't clamp either
        self._crop_origin = (box[0], box[1])
        self._region = (x, y, w, h)
        return raw.crop(box)

    def _to_physical(self, x: float, y: float) -> tuple[int, int]:
        """Model coords -> logical screen coords for pyautogui, clamped to scope."""
        if self._scale not in (0, 1.0):
            x, y = x / self._scale, y / self._scale
        px = (x + self._crop_origin[0]) / self._backing
        py = (y + self._crop_origin[1]) / self._backing
        if self._region is not None:
            rx, ry, rw, rh = self._region
            px = min(max(px, rx), rx + rw - 1)
            py = min(max(py, ry), ry + rh - 1)
        return int(px), int(py)

    # --- actions -----------------------------------------------------------
    def launch(self, name: str) -> None:
        """Open or focus a target — app name, URL, or file path.

        `open -a` resolves apps by name; plain `open` covers URLs, files and
        folders. Trying both makes 'open chrome', 'open ~/Downloads' and
        'open https://…' all work without the model picking the right verb.
        """
        system = platform.system()
        if system == "Darwin":
            try:
                subprocess.run(["open", "-a", name], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                subprocess.run(["open", name], check=True, capture_output=True)
        elif system == "Windows":
            os.startfile(name)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([name])
        time.sleep(self.action_delay)

    def move(self, x: float, y: float) -> None:
        px, py = self._to_physical(x, y)
        self.pyautogui.moveTo(px, py, duration=0.1)

    def click_at(self, x: float, y: float) -> None:
        """Click logical screen coords directly — the fast loop's elements are
        already in logical space, so no screenshot transform applies."""
        self.pyautogui.click(int(x), int(y))
        time.sleep(self.action_delay)

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
