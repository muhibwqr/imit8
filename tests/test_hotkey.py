from imit8.hotkey import ModifierChord


def make_chord(fired: list, grace: float = 0.4) -> ModifierChord:
    return ModifierChord({"ctrl", "alt"}, lambda: fired.append(True), grace=grace)


def test_modifier_tap_fires():
    fired = []
    chord = make_chord(fired)
    chord.press("ctrl", now=0.0)
    chord.press("alt", now=0.1)
    chord.release("alt", now=0.2)
    assert fired == [True]


def test_longer_shortcut_does_not_fire():
    fired = []
    chord = make_chord(fired)
    chord.press("ctrl", now=0.0)
    chord.press("alt", now=0.1)
    chord.press("space", now=0.2)  # ctrl+alt+space is a real shortcut, not a tap
    chord.release("space", now=0.3)
    chord.release("alt", now=0.35)
    chord.release("ctrl", now=0.4)
    assert fired == []


def test_held_chord_does_not_fire():
    fired = []
    chord = make_chord(fired)
    chord.press("ctrl", now=0.0)
    chord.press("alt", now=0.1)
    chord.release("alt", now=1.0)  # held past the grace window
    assert fired == []


def test_chord_rearms_after_a_combo():
    fired = []
    chord = make_chord(fired)
    chord.press("ctrl", now=0.0)
    chord.press("alt", now=0.1)
    chord.press("right", now=0.2)
    for key, t in [("right", 0.3), ("alt", 0.4), ("ctrl", 0.5)]:
        chord.release(key, now=t)
    assert fired == []
    chord.press("ctrl", now=1.0)
    chord.press("alt", now=1.1)
    chord.release("ctrl", now=1.2)
    assert fired == [True]
