import os

import pytest

from playclock.db import DB
from playclock.emulog import RyujinxLog
from playclock.rules import DEFAULT_RULES, Rule
from playclock.tracker import Tracker
from playclock.winapi import Window

RULES = [Rule.from_dict(d) for d in DEFAULT_RULES]
HEADER = "00:00:00.294 |N| Application PrintSystemInfo: Ryujinx Version: 1.3.3\n"
LOAD = "00:00:04.385 |I| Loader Start: Application Loaded: 薩爾達傳說 智慧的再現 v1.0.0 [01008cf01baac000] [64-bit]\n"
PAUSE = "00:07:41.486 |I| GUI.WindowThread Emulation Pause: Emulation was paused\n"
RESUME = "00:08:02.900 |I| GUI.WindowThread Emulation Resume: Emulation was resumed\n"
END = "02:14:31.841 |W| HLE.GuestThread.90 KernelSvc : WaitProcessWideKeyAtomic() = TerminationRequested\n"


@pytest.fixture
def ryujinx(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))  # keep the real user's logs out of the test
    (tmp_path / "Logs").mkdir()
    exe = tmp_path / "Ryujinx.exe"
    log = tmp_path / "Logs" / "Ryujinx_1.3.3_2026-10-13_19-33-31.log"
    log.write_text(HEADER, encoding="utf-8")
    return str(exe), log


def append(log, text):
    with open(log, "a", encoding="utf-8") as f:
        f.write(text)


def test_game_pause_resume_and_end(ryujinx):
    exe, log = ryujinx
    r = RyujinxLog()
    assert r.poll(exe) == (None, False)          # Ryujinx open, no game yet
    append(log, LOAD)
    assert r.poll(exe) == ("薩爾達傳說 智慧的再現", False)
    append(log, PAUSE)
    assert r.poll(exe) == ("薩爾達傳說 智慧的再現", True)
    append(log, RESUME)
    assert r.poll(exe) == ("薩爾達傳說 智慧的再現", False)
    append(log, END * 3)
    assert r.poll(exe) == (None, False)          # back to the game list


def test_half_written_line_waits_for_the_rest(ryujinx):
    exe, log = ryujinx
    r = RyujinxLog()
    append(log, LOAD[:40])
    assert r.poll(exe) == (None, False)
    append(log, LOAD[40:])
    assert r.poll(exe)[0] == "薩爾達傳說 智慧的再現"


def test_new_log_after_restart_starts_clean(ryujinx, tmp_path):
    exe, log = ryujinx
    append(log, LOAD)
    r = RyujinxLog()
    assert r.poll(exe)[0] == "薩爾達傳說 智慧的再現"
    newer = tmp_path / "Logs" / "Ryujinx_1.3.3_2026-10-13_21-00-00.log"
    newer.write_text(HEADER, encoding="utf-8")
    os.utime(newer, (log.stat().st_mtime + 10, log.stat().st_mtime + 10))
    assert r.poll(exe) == (None, False)


def test_tracker_counts_only_unpaused_play(ryujinx, tmp_path):
    exe, log = ryujinx
    append(log, LOAD)
    db = DB(tmp_path / "t.db")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0, "resume_gap": 0})
    win = [Window(1, 9, "ryujinx.exe", "Ryujinx 1.3.3", 10 ** 6, False, True, exe)]   # title has no game
    for s in range(0, 65, 5):
        t.tick(s, win)
    assert t.current.game == "薩爾達傳說 智慧的再現" and t.current.seconds == 60
    append(log, PAUSE)
    for s in range(65, 300, 5):                  # paused: nothing counted
        t.tick(s, win)
    assert t.current is None
    append(log, RESUME)
    for s in range(300, 335, 5):
        t.tick(s, win)
    t.flush()
    assert sum(r["seconds"] for r in db.sessions()) == 60 + 30
    assert {r["emulator"] for r in db.sessions()} == {"Ryujinx"}


def test_title_with_game_still_wins(ryujinx, tmp_path):
    exe, log = ryujinx
    append(log, LOAD)
    db = DB(tmp_path / "t.db")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0})
    t.tick(0, [Window(1, 9, "ryujinx.exe", "Ryujinx 1.1.1403 - Other Game v1.0.0 (64-bit)", 10 ** 6, False, True, exe)])
    assert t.current.game == "Other Game"
