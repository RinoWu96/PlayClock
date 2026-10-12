import json

from playclock.db import DB
from playclock.importers import apply_import, parse_timespan, parse_utc, pending_seconds, plan_import, scan_ryujinx
from playclock.rules import DEFAULT_RULES, Rule
from playclock.tracker import Tracker
from playclock.winapi import Window


def write_meta(root, tid, title, span, last):
    d = root / "games" / tid / "gui"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(
        {"title": title, "favorite": False, "timespan_played": span, "last_played_utc": last}), encoding="utf-8")


def test_parsers():
    assert parse_timespan("07:28:14.6243573") == 7 * 3600 + 28 * 60 + 14
    assert parse_timespan("1.02:00:00") == 26 * 3600
    assert parse_timespan("00:00:00") == 0 and parse_timespan("") == 0
    assert parse_utc("2026-10-12T10:43:00.0767533Z") == 1791801780.0
    assert parse_utc(None) is None


def test_scan_skips_unplayed(tmp_path):
    write_meta(tmp_path, "0100000000010000", "SUPER MARIO ODYSSEY", "00:00:00", None)
    write_meta(tmp_path, "01001", "薩爾達傳說 智慧的再現", "07:28:14.62", "2026-10-12T10:43:00Z")
    games = scan_ryujinx(tmp_path)
    assert [(g.title, g.seconds) for g in games] == [("薩爾達傳說 智慧的再現", 26894.0)]


def test_import_is_idempotent_and_never_double_counts(tmp_path):
    root = tmp_path / "ryu"
    write_meta(root, "01001", "Game A", "02:00:00", "2026-10-12T10:00:00Z")
    db = DB(tmp_path / "t.db")
    # PlayClock already tracked 30 min of Game A itself.
    sid = db.start_session("Game A", "Ryujinx", 1000)
    db.update_session(sid, 2800, 1800)

    plan = plan_import(db, scan_ryujinx(root))
    assert [(r.game, r.add) for r in plan] == [("Game A", 5400)]
    assert pending_seconds(db, plan) == 5400
    apply_import(db, plan)
    assert db.game_detail("Game A")["total"] == 7200          # = Ryujinx's own total
    assert pending_seconds(db, plan_import(db, scan_ryujinx(root))) == 0

    apply_import(db, plan_import(db, scan_ryujinx(root)))     # again: nothing added twice
    assert db.game_detail("Game A")["total"] == 7200

    # Played more in Ryujinx while PlayClock wasn't running -> only the new part is pending.
    (root / "games" / "01001" / "gui" / "metadata.json").unlink()
    (root / "games" / "01001" / "gui").rmdir()
    (root / "games" / "01001").rmdir()
    write_meta(root, "01001", "Game A", "03:00:00", "2026-10-13T10:00:00Z")
    assert pending_seconds(db, plan_import(db, scan_ryujinx(root))) == 3600


def test_import_follows_aliases_and_survives_tracking(tmp_path):
    root = tmp_path / "ryu"
    write_meta(root, "01001", "Raw Title", "01:00:00", "2026-10-12T10:00:00Z")
    db = DB(tmp_path / "t.db")
    sid = db.start_session("Raw Title", "Ryujinx", 0)
    db.update_session(sid, 600, 600)
    db.rename_game("Raw Title", "Nice Name")
    apply_import(db, plan_import(db, scan_ryujinx(root)))
    assert [r["game"] for r in db.game_stats()] == ["Nice Name"]
    assert db.game_detail("Nice Name")["total"] == 3600

    # Tracking must not resume into the imported session, and startup cleanup must keep it.
    rules = [Rule.from_dict(d) for d in DEFAULT_RULES]
    t = Tracker(db, rules, {"interval": 5, "min_session": 0, "resume_gap": 10 ** 12})
    w = Window(1, 1, "ryujinx.exe", "Ryujinx 1.1 - Raw Title", 1000, False, True)
    t.tick(2_000_000_000, [w])
    assert db.get_session(t.current.sid)["source"] is None
    db.drop_short(10 ** 9)
    assert db.imported_total("Nice Name", "ryujinx") == 3000
