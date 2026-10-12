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


def test_english_title_keeps_latin_tail():
    from playclock.covers import english_with_tail
    assert english_with_tail("Dragon Quest III", "勇者鬥惡龍III HD-2D Remake") == "Dragon Quest III HD-2D Remake"
    assert english_with_tail("The Legend of Zelda: Echoes of Wisdom", "薩爾達傳說 智慧的再現") == \
        "The Legend of Zelda: Echoes of Wisdom"
    assert english_with_tail("Octopath Traveler (video game)", "八方旅人") == "Octopath Traveler"


def test_platforms_are_counted_separately(tmp_path):
    from playclock.importers import EmuGame
    db = DB(tmp_path / "t.db")
    sid = db.start_session("OCTOPATH TRAVELER", "Ryujinx", 0)   # tracked on the Switch emulator
    db.update_session(sid, 3600, 3600)
    steam = [EmuGame("OCTOPATH TRAVELER", 7200, 10_000), EmuGame("OCTOPATH TRAVELER", 600, 20_000)]
    plan = plan_import(db, steam, emulator="Steam")
    assert [(r.game, r.emulator_total, r.tracked, r.add, r.last_played) for r in plan] == \
        [("OCTOPATH TRAVELER", 7800, 0, 7800, 20_000)]
    apply_import(db, plan, emulator="Steam", source="steam:1")
    assert db.game_detail("OCTOPATH TRAVELER")["total"] == 3600 + 7800


def test_steam_local_files(tmp_path):
    import struct
    from playclock.steam import SteamAccount, parse_vdf, playtimes, read_appinfo, scan_steam
    cfg = tmp_path / "userdata" / "42" / "config" / "localconfig.vdf"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('''"UserLocalConfigStore"
{
    "friends" { "PersonaName" "Me" }
    "Software" { "Valve" { "Steam" { "apps"
    {
        "10" { "LastPlayed" "1700000000" "Playtime" "90" "cloud" { "last_sync_state" "synchronized" } }
        "20" { "Playtime" "30" }
        "30" { "Playtime" "0" }
        "40" { "Playtime" "15" }
        "50" { "Playtime" "5" }
    } } } }
}''', encoding="utf-8")
    assert parse_vdf('"a" { "B" "c" }') == {"a": {"b": "c"}}
    assert playtimes(cfg) == {10: (5400.0, 1700000000.0), 20: (1800.0, None), 40: (900.0, None), 50: (300.0, None)}

    # Minimal v29 appinfo.vdf: app 10 = game with a Chinese name, 20 = tool, 40/50 not cached.
    strings = ["appinfo", "common", "name", "type", "name_localized", "schinese"]
    idx = {s: i for i, s in enumerate(strings)}

    def kv(d):
        out = b""
        for k, v in d.items():
            if isinstance(v, dict):
                out += b"\x00" + struct.pack("<I", idx[k]) + kv(v) + b"\x08"
            else:
                out += b"\x01" + struct.pack("<I", idx[k]) + v.encode() + b"\0"
        return out

    def app(appid, common):
        body = kv({"appinfo": {"common": common}}) + b"\x08"
        meta = struct.pack("<IIQ", 0, 0, 0) + b"\0" * 20 + struct.pack("<I", 0) + b"\0" * 20
        return struct.pack("<II", appid, len(meta) + len(body)) + meta + body

    apps = app(10, {"name": "Witcher", "type": "Game", "name_localized": {"schinese": "巫师"}}) + \
        app(20, {"name": "Redist", "type": "Tool"})
    head_len = 16
    table_off = head_len + len(apps) + 4
    table = struct.pack("<I", len(strings)) + b"".join(s.encode() + b"\0" for s in strings)
    blob = struct.pack("<IIq", 0x07564429, 1, table_off) + apps + struct.pack("<I", 0) + table
    (tmp_path / "appcache").mkdir()
    (tmp_path / "appcache" / "appinfo.vdf").write_bytes(blob)
    assert read_appinfo(tmp_path / "appcache" / "appinfo.vdf")[20] == ("Redist", "tool", {})

    acc = SteamAccount("42", "Me", cfg)
    games, unknown = scan_steam(tmp_path, acc, "zh", known_names={40: "Looked Up"})
    assert [(g.title, g.seconds) for g in games] == [("巫师", 5400.0), ("Looked Up", 900.0)]
    assert unknown == [50]
    assert scan_steam(tmp_path, acc, "en")[0][0].title == "Witcher"


def test_imports_count_in_totals_but_not_in_dated_stats(tmp_path):
    import time as _t
    from playclock.importers import EmuGame
    db = DB(tmp_path / "t.db")
    now = _t.time()
    apply_import(db, plan_import(db, [EmuGame("Big Game", 1000 * 3600, now - 3600)], "Steam"), "Steam", "steam:1")
    db.add_manual("Console Game", "PS5", 7200, now - 60)
    assert {r["game"]: r["total"] for r in db.game_stats()} == {"Big Game": 3600000, "Console Game": 7200}
    assert db.total_since(now - 86400) == 7200                  # today: hand-entered only
    assert db.played_days() == 1
    assert sum(s for _, s in db.daily(7)) == 7200 and sum(s for _, s in db.monthly(2)) == 7200
    assert [r["game"] for r in db.game_stats(now - 86400)] == ["Console Game"]
    assert db.game_detail("Big Game")["first"] is None and db.game_detail("Big Game")["last"] == now - 3600
    assert db.has_imports()
