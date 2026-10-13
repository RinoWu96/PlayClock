from playclock.db import DB
from playclock.rules import DEFAULT_RULES, Rule
from playclock.tracker import Tracker
from playclock.winapi import Window

RULES = [Rule.from_dict(d) for d in DEFAULT_RULES]


def game(exe, title):
    rule = next(r for r in RULES if r.matches_exe(exe))
    return rule.game_from_title(title)


def test_titles():
    cases = [
        ("Ryujinx.exe", "Ryujinx 1.1.1403 - The Legend of Zelda: Tears of the Kingdom v1.2.1 (0100F2C0115B6000) (64-bit)",
         "The Legend of Zelda: Tears of the Kingdom"),
        ("Ryujinx.exe", "Ryujinx 1.2.86-ldn3.1.3 | 超级马力欧兄弟 惊奇 v1.0.0 (010015100B514000) (64-bit)", "超级马力欧兄弟 惊奇"),
        ("Ryujinx.exe", "Ryujinx 1.1.1403", None),
        ("yuzu.exe", "yuzu 1734 | Super Mario Odyssey | 1.3.0 | 64-bit | Vulkan | NVIDIA GeForce", "Super Mario Odyssey"),
        ("yuzu.exe", "yuzu 1734", None),
        ("eden.exe", "Eden | v0.0.3 | 1-2-Switch | 1.0.0 | 64-bit | Vulkan", "1-2-Switch"),
        ("azahar.exe", "Azahar 2121 | Pokémon X | Vulkan", "Pokémon X"),
        ("citra-qt.exe", "Citra Nightly 1985 | Pokémon Ultra Sun", "Pokémon Ultra Sun"),
        ("citra-qt.exe", "Citra Canary 2766 | HEAD-1a2b3c4 | 塞尔达传说 时之笛3D", "塞尔达传说 时之笛3D"),
        ("citra-qt.exe", "Citra | HEAD-1a2b3c4 | Monster Hunter 4 Ultimate (0004000000126300)", "Monster Hunter 4 Ultimate"),
        ("citra-qt.exe", "Citra Nightly 1985", None),
        ("citra-qt.exe", "Citra | HEAD-1a2b3c4", None),
        ("lime3ds-gui.exe", "Lime3DS 2119.1 | Fire Emblem Awakening", "Fire Emblem Awakening"),
        ("Dolphin.exe", "Dolphin 2412 | JIT64 DC | Vulkan | HLE | Super Smash Bros. Melee (GALE01)", "Super Smash Bros. Melee"),
        ("Dolphin.exe", "Dolphin 2412", None),
        ("pcsx2-qt.exe", "Shadow of the Colossus", "Shadow of the Colossus"),
        ("pcsx2-qt.exe", "PCSX2 v2.2.0", None),
        ("pcsx2-qt.exe", "Controller Settings", None),
        ("duckstation-qt-x64-ReleaseLTCG.exe", "Final Fantasy VII (Disc 1)", "Final Fantasy VII (Disc 1)"),
        ("PPSSPPWindows64.exe", "PPSSPP v1.17.1 - ULUS10041 : Monster Hunter Freedom Unite", "Monster Hunter Freedom Unite"),
        ("rpcs3.exe", "FPS: 59.94 | Vulkan | 0.0.33 | Demon's Souls [BLUS30443]", "Demon's Souls"),
        ("Cemu.exe", "Cemu 2.4 - FPS: 59.94 [TitleId: 00050000-101c9400] Breath of the Wild [US v208]", "Breath of the Wild"),
        ("melonDS.exe", "melonDS 0.9.5 - Pokemon Platinum.nds", "Pokemon Platinum"),
        ("mGBA.exe", "mGBA - Pokemon Emerald (60 fps) - 0.10.3", "Pokemon Emerald"),
        ("mGBA.exe", "mGBA - 0.10.3", None),
    ]
    for exe, title, want in cases:
        assert game(exe, title) == want, (exe, title, game(exe, title))


def win(title, exe="Ryujinx.exe", fg=True, minimized=False):
    return Window(1, 100, exe.lower(), title, 1000, minimized, fg)


def test_tracking(tmp_path):
    db = DB(tmp_path / "t.db")
    cfg = {"interval": 5, "min_session": 60, "resume_gap": 180}
    t = Tracker(db, RULES, cfg)
    playing = [win("Ryujinx 1.1 - Game A v1.0 (0100F2C0115B6000) (64-bit)")]
    for i in range(0, 125, 5):
        t.tick(1000.0 + i, playing)
    assert t.current.game == "Game A" and t.current.seconds == 120
    # PC sleeps for an hour: gap is not counted, a new session starts.
    t.tick(1000.0 + 120 + 3600, playing)
    assert db.game_stats()[0]["total"] == 120
    # Emulator closed after 10 s in the new session: that short session is dropped.
    t.tick(1000.0 + 120 + 3610, playing)
    t.tick(1000.0 + 120 + 3615, [])
    assert t.current is None
    stats = db.game_stats()
    assert len(stats) == 1 and stats[0]["n"] == 1 and stats[0]["total"] == 120


def test_resume_and_rename(tmp_path):
    db = DB(tmp_path / "t.db")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0, "resume_gap": 180})
    playing = [win("Ryujinx 1.1 - Game A")]
    for i in range(0, 65, 5):
        t.tick(i, playing)
    t.tick(70, [])                      # closed briefly
    for i in range(100, 135, 5):        # reopened within resume_gap -> same session
        t.tick(i, playing)
    assert len(db.sessions()) == 1 and db.sessions()[0]["seconds"] == 90
    db.rename_game("Game A", "游戏甲")
    t.tick(140, playing)
    t.tick(145, playing)
    t.flush()
    assert [r["game"] for r in db.game_stats()] == ["游戏甲"]
    assert db.game_stats()[0]["total"] == 95


def test_foreground_only(tmp_path):
    db = DB(tmp_path / "t.db")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0, "foreground_only": True})
    t.tick(0, [win("Ryujinx 1.1 - Game A", fg=False)])
    assert t.current is None
    t.tick(5, [win("Ryujinx 1.1 - Game A", minimized=True)])
    assert t.current is None
    t.tick(10, [win("Ryujinx 1.1 - Game A")])
    assert t.current.game == "Game A"


def test_user_rules(tmp_path):
    import json
    from playclock.rules import ensure_user_file, load_rules
    path = tmp_path / "rules.user.json"
    ensure_user_file(path)
    assert [r.name for r in load_rules(path)] == [d["name"] for d in DEFAULT_RULES]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rules"] = [
        {"name": "Ryujinx", "exe": ["ryujinx*.exe"], "pattern": r"^MY (?P<game>.+)$"},  # override
        {"name": "RetroArch", "disabled": True},                                         # disable
        {"name": "MyEmu", "exe": ["myemu.exe"]},                                          # add
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    rules = load_rules(path)
    names = [r.name for r in rules]
    assert names[:2] == ["Ryujinx", "MyEmu"] and "RetroArch" not in names and names.count("Ryujinx") == 1
    ryu = rules[0]
    assert ryu.game_from_title("MY Game") == "Game" and ryu.game_from_title("Ryujinx 1.1 - Game") is None
    assert load_rules(None)[0].name == DEFAULT_RULES[0]["name"]


def test_writes_are_batched(tmp_path):
    db = DB(tmp_path / "t.db")
    t = Tracker(db, RULES, {"interval": 5, "min_session": 0})
    playing = [win("Ryujinx 1.1 - Game A")]
    for i in range(0, 30, 5):          # 25 s of play: still only in memory
        t.tick(i, playing)
    assert db.sessions()[0]["seconds"] == 0 and t.current.seconds == 25
    t.tick(30, playing)                # 30 s since the row was written -> flushed
    assert db.sessions()[0]["seconds"] == 30
    t.tick(35, playing)
    t.tick(40, [])                     # game closed -> final state written
    assert db.sessions()[0]["seconds"] == 35 and db.sessions()[0]["end"] == 35


def test_pc_game_added_by_hand_is_tracked_by_full_path(tmp_path):
    from playclock.pc import is_steam_game, suggest_name
    db = DB(tmp_path / "t.db")
    cfg = {"interval": 5, "min_session": 60, "resume_gap": 180,
           "pc_games": [{"path": r"D:\Games\Hollow Knight\hollow_knight.exe", "name": "Hollow Knight"}]}
    t = Tracker(db, RULES, cfg)
    game = Window(2, 200, "hollow_knight.exe", "Hollow Knight", 2000, False, True,
                  path=r"d:\games\HOLLOW KNIGHT\hollow_knight.exe")               # case doesn't matter
    other = Window(3, 300, "hollow_knight.exe", "Hollow Knight", 2000, False, True,
                   path=r"E:\Copy\hollow_knight.exe")                               # same exe name elsewhere: no
    for i in range(0, 125, 5):
        t.tick(1000.0 + i, [game])
    assert t.current.game == "Hollow Knight" and t.current.emulator == "PC" and t.current.seconds == 120
    t.stop()
    t.tick(2000, [other])
    assert t.current is None
    t.tick(3000, [Window(2, 200, "hollow_knight.exe", "Hollow Knight", 2000, True, False,
                         path=r"D:\Games\Hollow Knight\hollow_knight.exe")])       # minimized: not playing
    assert t.current is None
    assert [r["emulator"] for r in db.con.execute("SELECT emulator FROM sessions")] == ["PC"]

    assert is_steam_game(r"C:\Program Files (x86)\Steam\steamapps\common\Hades\Hades.exe")
    assert not is_steam_game(r"D:\Games\Hades\Hades.exe")
    assert suggest_name(r"D:\Games\Elden Ring\Game\eldenring.exe") == "Elden Ring"
    assert suggest_name(r"D:\Games\Some Game\Binaries\Win64\SomeGame-Win64-Shipping.exe") == "Some Game"
    assert suggest_name(r"D:\game.exe") == "game"
