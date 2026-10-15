"""One name per game across languages (Switch title id / Steam appid), from the user's real cases."""
import json
import time

from playclock.db import DB, bad_name
from playclock.importers import EmuGame, apply_import, plan_import
from playclock.rules import DEFAULT_RULES, Rule
from playclock.sync import decode, encode, export_payload, merge_payload
from playclock.tracker import Tracker
from playclock.unify import unify, unify_steam
from playclock.winapi import Window

RULES = [Rule.from_dict(d) for d in DEFAULT_RULES]
EOW = "01008cf01baac000"     # Echoes of Wisdom
BOTW = "01007ef00011e000"    # Breath of the Wild: Ryujinx can't read its name
GARBLED = "E\x01��J�`"


def ryujinx(tmp_path, titles: dict, logs: list[list[tuple[str, str]]]):
    """A Ryujinx data folder (games/<tid>/gui/metadata.json) and log folder."""
    root = tmp_path / "Ryujinx"
    for tid, (title, span) in titles.items():
        meta = root / "games" / tid / "gui" / "metadata.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({"title": title, "timespan_played": span,
                                    "last_played_utc": "2026-10-15T10:00:00Z"}), encoding="utf-8")
    log_dir = tmp_path / "Logs"
    log_dir.mkdir()
    for i, loads in enumerate(logs):
        f = log_dir / f"Ryujinx_1.3.3_2026-10-1{i}_00-00-00.log"
        f.write_text("".join(f"00:00:04 |I| Loader Start: Application Loaded: {name} v1.0.0 [{tid}] [64-bit]\n"
                             for name, tid in loads), encoding="utf-8")
        t = time.time() - 1000 + i * 100
        import os
        os.utime(f, (t, t))
    return root, log_dir


def total(db, game):
    return db.game_detail(game)["total"]


def test_bad_names():
    assert bad_name("<Unknown Name>") and bad_name(GARBLED) and bad_name("") and bad_name("a\x01b")
    assert not bad_name("薩爾達傳說 智慧的再現") and not bad_name("OCTOPATH TRAVELER")


def test_language_switch_is_merged_and_not_double_counted(tmp_path):
    db = DB(tmp_path / "pc.db", device="PC")
    # before: Ryujinx showed Traditional Chinese; PlayClock tracked 2 h and imported the rest under that name
    sid = db.start_session("薩爾達傳說 智慧的再現", "Ryujinx", 0)
    db.update_session(sid, 7200, 7200)
    apply_import(db, plan_import(db, [EmuGame("薩爾達傳說 智慧的再現", 30000, 1)]))
    # then Ryujinx's language changed: its total (40000 s) is now under the Simplified name and got imported in full
    apply_import(db, plan_import(db, [EmuGame("塞尔达传说 智慧的再现", 40000, 2)]))
    assert total(db, "薩爾達傳說 智慧的再現") + total(db, "塞尔达传说 智慧的再现") == 7200 + 22800 + 40000  # the bug

    root, logs = ryujinx(tmp_path, {EOW: ("塞尔达传说 智慧的再现", "11:06:40")},
                         [[("薩爾達傳說 智慧的再現", EOW)], [("塞尔达传说 智慧的再现", EOW)]])
    assert unify(db, [logs], [root]) == 1
    assert db.games() == ["塞尔达传说 智慧的再现"]                    # the name Ryujinx shows now
    assert total(db, "塞尔达传说 智慧的再现") == 40000                 # = Ryujinx's own total, once
    assert db.resolve("薩爾達傳說 智慧的再現") == "塞尔达传说 智慧的再现"


def test_live_tracking_follows_the_title_id(tmp_path, monkeypatch):
    db = DB(tmp_path / "pc.db", device="PC")
    db.canonical("塞尔达传说 智慧的再现", f"switch:{EOW}")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0})
    # an old-style window title in English with the title id: still the same game
    win = [Window(1, 9, "ryujinx.exe", f"Ryujinx 1.1 - The Legend of Zelda: Echoes of Wisdom v1.0.0 ({EOW.upper()}) (64-bit)",
                  10 ** 6, False, True)]
    t.tick(0, win)
    t.tick(5, win)
    assert t.current.game == "塞尔达传说 智慧的再现"
    assert db.resolve("The Legend of Zelda: Echoes of Wisdom") == "塞尔达传说 智慧的再现"


def test_garbled_title_uses_the_users_rename(tmp_path):
    db = DB(tmp_path / "pc.db", device="PC")
    db.replace_import(GARBLED, "Ryujinx", "ryujinx", 720, 1)          # an old version imported the garbled name
    db.rename_game(GARBLED, "塞尔达传说 旷野之息")                       # the user fixed it by hand
    root, logs = ryujinx(tmp_path, {BOTW: (GARBLED, "00:12:00")}, [[("<Unknown Name>", BOTW)]])
    unify(db, [logs], [root])
    assert db.games() == ["塞尔达传说 旷野之息"] and total(db, "塞尔达传说 旷野之息") == 720
    assert db.canonical("<Unknown Name>", f"switch:{BOTW}") == "塞尔达传说 旷野之息"   # live: log says <Unknown Name>
    assert db.canonical("<Unknown Name>") is None                                       # no id: not a game
    assert plan_import(db, [EmuGame(GARBLED, 900, 1)])[0].game == "塞尔达传说 旷野之息"  # known garbled name: via the rename
    assert plan_import(db, [EmuGame("�new", 900, 1)]) == []                   # unknown garbled, no id: skipped


def test_steam_names_in_other_languages_are_merged(tmp_path):
    db = DB(tmp_path / "pc.db", device="PC")
    apply_import(db, plan_import(db, [EmuGame("ELDEN RING", 3600, 1)], "Steam"), "Steam", "steam:1")       # imported in English
    apply_import(db, plan_import(db, [EmuGame("艾尔登法环", 3600, 1)], "Steam"), "Steam", "steam:1")       # later in Chinese
    sid = db.start_session("Elden Ring", "Ryujinx", 0)       # a different platform's game with a similar name stays apart
    db.update_session(sid, 60, 60)
    unify_steam(db, {1245620: "艾尔登法环"}, {1245620: ["ELDEN RING", "艾尔登法环", "艾爾登法環"]})
    apply_import(db, plan_import(db, [EmuGame("艾尔登法环", 3600, 1, key="steam:1245620")], "Steam"), "Steam", "steam:1")
    assert sorted(db.games()) == ["Elden Ring", "艾尔登法环"] and total(db, "艾尔登法环") == 3600


def test_devices_agree_on_one_name(tmp_path):
    a, b = DB(tmp_path / "a.db", device="A"), DB(tmp_path / "b.db", device="B")
    key = f"switch:{EOW}"
    a.canonical("塞尔达传说 智慧的再现", key)
    time.sleep(0.01)
    b.canonical("薩爾達傳說 智慧的再現", key)            # B first saw it later, under another language
    for db, name in ((a, "薩爾達傳說 智慧的再現"), (b, "塞尔达传说 智慧的再现")):
        sid = db.start_session(name, "Ryujinx", 0)
        db.update_session(sid, 100, 100)
    for _ in range(2):
        merge_payload(b, decode(encode(export_payload(a, "a"))))
        merge_payload(a, decode(encode(export_payload(b, "b"))))
    assert a.games() == b.games() == ["塞尔达传说 智慧的再现"]      # the earliest automatic choice wins everywhere
    assert total(a, "塞尔达传说 智慧的再现") == total(b, "塞尔达传说 智慧的再现") == 200
    a.rename_game("塞尔达传说 智慧的再现", "智慧的再现")                # a user rename is newest: it wins too
    merge_payload(b, decode(encode(export_payload(a, "a"))))
    assert b.games() == ["智慧的再现"] and b.canonical("薩爾達傳說 智慧的再現", key) == "智慧的再现"
