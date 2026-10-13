import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from playclock.emulog import RyujinxLog
from playclock.tracker import Current, Detection


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_accent_comes_from_the_cover(qapp):
    from playclock.ui.overlay import accent_of
    pm = QPixmap(64, 36)
    pm.fill(QColor("#c0392b"))                 # a red cover
    c = accent_of("Red Game", pm)
    assert c.hsvHue() < 15 or c.hsvHue() > 345
    assert accent_of("No Cover", None).isValid()


class FakeDB:
    def resolve(self, g):
        return g

    def daily(self, days, game):
        return [("2026-10-14", 1800.0)]

    def game_detail(self, game):
        return {"total": 7200.0}


def fake_ctrl(**tracker):
    t = SimpleNamespace(**{"current": None, "last_tick": time.time(), "logs": {}, "detections": [], **tracker})
    return SimpleNamespace(tracker=t, db=FakeDB(), cfg={"interval": 5, "overlay_pos": None},
                           cover=lambda g: None, show_window=lambda: None, set_overlay=lambda on: None,
                           set_cfg=lambda k, v: None, add_running_pc_game=lambda parent: False)


def paused_log():
    r = RyujinxLog()
    r.game, r.paused = "Zelda", True
    return r


@pytest.mark.parametrize("tracker, expect", [
    ({"current": Current(1, "Zelda", "Ryujinx", 125.0)}, ("recording", "Zelda", "Ryujinx")),
    ({"logs": {"x": paused_log()}}, ("paused", "Zelda", "Ryujinx")),
    ({"detections": [Detection("PC", "BIOHAZARD", "BIOHAZARD® PC", False)]}, ("background", "BIOHAZARD", "PC")),
    ({}, ("none", None, "")),
])
def test_states_and_painting(qapp, tracker, expect):
    from playclock.ui.overlay import Overlay
    o = Overlay(fake_ctrl(**tracker))
    kind, game, emulator, seconds = o.state()
    assert (kind, game, emulator) == expect
    if kind == "recording":
        assert seconds >= 125
    img = o.grab().toImage()                    # paints without errors in every state
    assert img.width() == Overlay.W
    assert o.buttons["add"].isVisible() == (kind == "none") or not o.isVisible()
    o.deleteLater()
