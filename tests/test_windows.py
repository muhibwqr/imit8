from imit8.computer import Computer, ScopeRegion
from imit8.windows import _pick


def window(pid, owner="Safari", layer=0, rect=(100, 100, 800, 600)):
    return {
        "kCGWindowOwnerPID": pid,
        "kCGWindowOwnerName": owner,
        "kCGWindowName": "tab",
        "kCGWindowNumber": 42,
        "kCGWindowLayer": layer,
        "kCGWindowBounds": {"X": rect[0], "Y": rect[1], "Width": rect[2], "Height": rect[3]},
    }


def test_pick_skips_own_pid_and_system_windows():
    ours = window(1, owner="python3.14")
    dock = window(2, owner="Dock")
    theirs = window(3, owner="Safari")
    picked = _pick([ours, dock, theirs], exclude_pid=1)
    assert picked.app == "Safari" and picked.pid == 3


def test_pick_skips_tiny_and_nonzero_layer():
    tiny = window(1, rect=(0, 0, 10, 10))
    menubar = window(2, owner="SystemUIServer", layer=24)
    real = window(3)
    assert _pick([tiny, menubar, real], exclude_pid=0).pid == 3


def test_pick_returns_none_when_nothing_fits():
    assert _pick([window(1, owner="Dock")], exclude_pid=0) is None


class FakeGrab:
    """A 400x200 pixel screen presented as 200x100 logical points (backing=2)."""

    def __init__(self):
        from PIL import Image

        self.image = Image.new("RGB", (400, 200), (10, 20, 30))


class StubPyautogui:
    def size(self):
        return (200, 100)

    def __getattr__(self, name):
        return lambda *a, **k: None


def scoped_computer(scope=None):
    computer = Computer(target_width=1000)
    computer._pyautogui = StubPyautogui()
    grab = FakeGrab()
    computer._grab = lambda: grab.image
    computer.scope = scope
    return computer


def test_scoped_screenshot_crops_to_the_window():
    # logical rect (10,10,40,20) -> pixel box (20,20,100,60) at backing=2
    computer = scoped_computer(ScopeRegion((10, 10, 40, 20), app="Safari"))
    shot = computer.screenshot()
    assert (shot.width, shot.height) == (80, 40)
    assert computer._region == (10, 10, 40, 20)


def test_scoped_click_maps_model_coords_into_the_window():
    computer = scoped_computer(ScopeRegion((10, 10, 40, 20)))
    computer.screenshot()  # scale=1 (80px < 1000 target), crop origin (20,20), backing=2
    # model coord (0,0) = the crop's top-left = raw px (20,20) -> logical (10,10)
    assert computer._to_physical(0, 0) == (10, 10)
    # model coord (40,20) -> raw px (60,40) -> logical (30,20), inside the region
    assert computer._to_physical(40, 20) == (30, 20)


def test_scoped_clicks_are_clamped_inside_the_window():
    computer = scoped_computer(ScopeRegion((10, 10, 40, 20)))
    computer.screenshot()
    # way off the crop clamps to the window's far edge (10+40-1, 10+20-1)
    assert computer._to_physical(9999, 9999) == (49, 29)
    assert computer._to_physical(-500, -500) == (10, 10)


def test_unscoped_capture_uses_logical_coords():
    computer = scoped_computer()
    shot = computer.screenshot()
    assert (shot.width, shot.height) == (400, 200)
    # raw pixel (200,100) -> logical (100,50) on a 2x display
    assert computer._to_physical(200, 100) == (100, 50)


def test_scope_offscreen_falls_back_to_full_capture():
    computer = scoped_computer(ScopeRegion((500, 500, 100, 100)))
    shot = computer.screenshot()
    assert (shot.width, shot.height) == (400, 200)  # uncropped
    assert computer._region is None  # and nothing is clamped


def test_scope_region_returns_its_frozen_rect():
    assert ScopeRegion((0, 0, 1, 1), app="x").bounds() == (0, 0, 1, 1)
