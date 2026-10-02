import platform

from imit8.elements import Element, frontmost_elements


def test_element_line_is_compact_state_text():
    el = Element(3, "AXButton", "Reload", 960, 44)
    assert el.line() == "[3] Button 'Reload' @(960,44)"


def test_press_without_a_ref_falls_back():
    # Elements built without an AX ref (tests, replays) can't AXPress —
    # the caller falls back to a coordinate click.
    assert Element(0, "AXButton", "OK", 10, 10).press() is False


def test_press_with_a_dead_ref_returns_false_not_exception():
    class Dead:
        pass

    assert Element(0, "AXButton", "OK", 10, 10, _ref=Dead()).press() is False


def test_frontmost_elements_off_macos_is_empty(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert frontmost_elements() == ("", [])
