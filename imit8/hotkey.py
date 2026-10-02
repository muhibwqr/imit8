"""Modifier-only hotkey detection, kept free of pynput so it stays testable."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable


class ModifierChord:
    """Fires when a chord of only modifier keys is tapped and released quickly.

    Pressing ctrl+option and letting go opens the spotlight, but the tap must
    complete within ``grace`` seconds and no other key may be pressed while the
    modifiers are held — so real shortcuts like ctrl+option+space or
    ctrl+option+arrow never trigger it. Keys are opaque values; the caller
    passes pynput-canonical keys.
    """

    def __init__(self, keys: Iterable, on_activate: Callable[[], None], grace: float = 0.4) -> None:
        self._keys = set(keys)
        self._on_activate = on_activate
        self._grace = grace
        self._pressed: set = set()
        self._armed_at: float | None = None
        self._used_in_combo = False

    def press(self, key, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if key in self._keys:
            self._pressed.add(key)
            if self._pressed == self._keys and self._armed_at is None:
                self._armed_at = now
        elif self._pressed:
            self._used_in_combo = True

    def release(self, key, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        armed_at, self._armed_at = self._armed_at, None
        self._pressed.discard(key)
        fire = (
            key in self._keys
            and armed_at is not None
            and not self._used_in_combo
            and now - armed_at <= self._grace
        )
        if not self._pressed:
            self._used_in_combo = False
        if fire:
            self._on_activate()
