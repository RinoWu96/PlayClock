"""The Steam Deck agent (deck/playclock_deck.py) and its contract with the Windows app's sync format."""
import importlib.util
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("playclock_deck", ROOT / "deck" / "playclock_deck.py")
deck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deck)

from playclock.db import DB  # noqa: E402
from playclock.rules import DEFAULT_RULES, Rule, platform_map  # noqa: E402
from playclock.sync import decode, encode, merge_payload, run_sync, WebDAV  # noqa: E402

ROMS = "/home/deck/Emulation/roms"


@pytest.mark.parametrize("comm, args, expect", [
    ("dolphin-emu", ["/app/bin/dolphin-emu", "-b", "-e", f"{ROMS}/gc/Super Smash Bros. Melee (USA) (En,Ja) (Rev 2).rvz"],
     ("Dolphin", "Super Smash Bros. Melee")),
    ("duckstation-qt", ["/app/bin/duckstation-qt", "-batch", "-fullscreen", f"{ROMS}/psx/Final Fantasy VII (USA) (Disc 1).chd"],
     ("DuckStation", "Final Fantasy VII")),
    ("Ryujinx", ["/tmp/.mount_ryujiAbC/usr/bin/Ryujinx", "--fullscreen", f"{ROMS}/switch/Super Mario Odyssey [0100000000010000][v0].nsp"],
     ("Ryujinx", "Super Mario Odyssey")),
    ("pcsx2-qt", ["/app/bin/pcsx2-qt", "-fullscreen", "-nogui", "--", f"{ROMS}/ps2/Shadow of the Colossus (USA).chd"],
     ("PCSX2", "Shadow of the Colossus")),
    ("retroarch", ["/app/bin/retroarch", "-L", "/home/deck/.var/app/org.libretro.RetroArch/cores/snes9x_libretro.so",
                   f"{ROMS}/snes/Chrono Trigger (USA).sfc"], ("RetroArch", "Chrono Trigger")),
    ("rpcs3", ["/tmp/.mount_rpcs3/usr/bin/rpcs3", "--no-gui", "/home/deck/Emulation/storage/rpcs3/dev_hdd0/game/BLUS30443/USRDIR/EBOOT.BIN"],
     ("RPCS3", "BLUS30443")),
    ("melonDS", ["/app/bin/melonDS", f"{ROMS}/nds/Pokemon - Platinum Version (USA).nds"], ("melonDS", "Pokemon - Platinum Version")),
])
def test_games_from_emudeck_style_command_lines(comm, args, expect):
    d = deck.Detector()
    assert d.detect([(100, comm, args)])[:2] == expect


def test_launch_scripts_and_idle_emulators_are_not_games():
    d = deck.Detector()
    script = (1, "bash", ["/bin/bash", "/home/deck/Emulation/tools/launchers/dolphin-emu.sh", f"{ROMS}/gc/Melee.rvz"])
    idle = (2, "dolphin-emu", ["/app/bin/dolphin-emu"])
    assert d.detect([script, idle]) is None
    assert [name for name, _, _ in d.seen] == ["Dolphin"]


def test_ryujinx_name_and_pause_come_from_its_log(tmp_path, monkeypatch):
    monkeypatch.setattr(deck, "HOME", tmp_path)
    logs = tmp_path / ".config" / "Ryujinx" / "Logs"
    logs.mkdir(parents=True)
    log = logs / "Ryujinx_1.3.3_2026-10-15_20-00-00.log"
    log.write_text("00:00:04.385 |I| Loader Start: Application Loaded: 薩爾達傳說 智慧的再現 v1.0.0 "
                   "[01008cf01baac000] [64-bit]\n", encoding="utf-8")
    proc = [(7, "Ryujinx", ["/tmp/.mount_x/usr/bin/Ryujinx", f"{ROMS}/switch/zelda-eow.nsp"])]
    d = deck.Detector()
    assert d.detect(proc) == ("Ryujinx", "薩爾達傳說 智慧的再現", "switch:01008cf01baac000")
    with open(log, "a", encoding="utf-8") as f:
        f.write("00:07:41.486 |I| GUI.WindowThread Emulation Pause: Emulation was paused\n")
    assert d.detect(proc) is None and d.paused == "薩爾達傳說 智慧的再現"


def test_tracker_skips_sleep_drops_short_and_resumes(tmp_path):
    store = deck.Store(tmp_path / "s.json", "deck1")
    t = deck.Tracker(store)
    game = ("melonDS", "Pokemon Platinum")
    for s in range(0, 130, 10):
        t.tick(s, game)
    assert t.current["seconds"] == 120
    t.tick(130 + 3600, game)              # the Deck slept an hour: not counted, a new session starts
    t.tick(3740, game)
    t.tick(3750, None)                    # closed after 10 s: too short, dropped with a tombstone
    assert [s["seconds"] for s in store.sessions] == [120] and len(store.tombstones) == 1
    t.tick(3760, game)                    # reopened, but the last kept session ended long ago: a new one
    t.tick(3770, game)
    assert len(store.sessions) == 2
    reloaded = deck.Store(tmp_path / "s.json", "deck1")
    assert len(reloaded.sessions) == 2   # saved to disk


def test_payload_merges_into_the_windows_app(tmp_path):
    store = deck.Store(tmp_path / "s.json", "deck1")
    t = deck.Tracker(store)
    for s in range(0, 400, 10):
        t.tick(1_800_000_000 + s, ("melonDS", "Pokemon Platinum"))
    t.tick(1_800_000_400, None)
    store.import_totals({"ryujinx": [("Zelda", 7200.0, 1_800_000_500.0, None, "Ryujinx")]})
    blob = deck.encode(store.payload("Steam Deck"))
    assert blob == encode(store.payload("Steam Deck"))          # byte-identical to the PC encoder
    pc = DB(tmp_path / "pc.db", device="PC")
    assert merge_payload(pc, decode(blob)) == 2
    totals = {r["game"]: r["total"] for r in pc.game_stats()}
    assert totals == {"Pokemon Platinum": 390.0, "Zelda": 7200.0}
    rules = [Rule.from_dict(d) for d in DEFAULT_RULES]
    assert platform_map(rules)["melonDS"] == "NDS"              # PC shows it under NDS


def test_import_takes_the_larger_source_not_the_sum(tmp_path):
    """Ryujinx's own total and Steam's shortcut play time describe the same play: never added together."""
    store = deck.Store(tmp_path / "s.json", "deck1")
    zelda = "switch:01008cf01baac000"
    sources = {"ryujinx": [("塞尔达传说 智慧的再现", 3600.0, 100.0, zelda, "Ryujinx")],
               "steam": [("The Legend of Zelda: Echoes of Wisdom", 4200.0, 200.0, zelda, "Ryujinx"),
                         ("Pokemon Platinum", 1800.0, 50.0, "steamshortcut:42", "melonDS")]}
    store.import_totals(sources)
    rows = {s["game"]: s for s in store.sessions}
    assert set(rows) == {"塞尔达传说 智慧的再现", "Pokemon Platinum"}           # one name per game id
    assert rows["塞尔达传说 智慧的再现"]["seconds"] == 4200 and rows["Pokemon Platinum"]["emulator"] == "melonDS"
    # tracked play on the Deck shrinks what the import adds; re-importing updates the same row
    store.sessions.append({"uid": "u1", "game": "塞尔达传说 智慧的再现", "emulator": "Ryujinx", "start": 0, "end": 600,
                           "seconds": 600.0, "source": None, "device": "deck1", "updated": 1})
    store.import_totals(sources)
    assert sum(s["seconds"] for s in store.sessions if s["game"] == "塞尔达传说 智慧的再现") == 4200
    assert len([s for s in store.sessions if s["source"] == "import"]) == 2


def test_ryujinx_totals_from_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(deck, "HOME", tmp_path)
    meta = tmp_path / ".config" / "Ryujinx" / "games" / "01008cf01baac000" / "gui" / "metadata.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"title": "Zelda", "timespan_played": "07:28:14.62",
                                "last_played_utc": "2026-10-12T10:43:00Z"}), encoding="utf-8")
    assert deck.ryujinx_totals() == [("Zelda", 26894.0, 1791801780.0, "switch:01008cf01baac000", "Ryujinx")]


@pytest.fixture
def dav_server(tmp_path):
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    root = tmp_path / "dav"
    root.mkdir()
    server = wsgi.Server(("127.0.0.1", 0), WsgiDAVApp({
        "provider_mapping": {"/dav": str(root)},
        "http_authenticator": {"domain_controller": None, "accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "simple_dc": {"user_mapping": {"*": {"me@example.com": {"password": "app-pass"}}}},
        "verbose": 0, "logging": {"enable": False}}))
    server.prepare()
    threading.Thread(target=server.serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.bind_addr[1]}/dav/"
    server.stop()


def test_deck_upload_reaches_the_windows_app(tmp_path, dav_server):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device_id": "deck1", "name": "Steam Deck",
                               "webdav": {"url": dav_server, "user": "me@example.com", "password": "app-pass"}}))
    agent = deck.Agent(cfg, tmp_path / "data")
    for s in range(0, 200, 10):
        agent.tracker.tick(1_800_000_000 + s, ("PPSSPP", "Monster Hunter Freedom Unite"))
    agent.tracker.tick(1_800_000_200, None)
    assert agent.sync().startswith("uploaded")
    assert agent.sync() == "up to date"                         # unchanged -> no upload
    with pytest.raises(deck.SyncError, match="auth"):
        deck.WebDAV(dav_server, "me@example.com", "wrong").check()

    pc = DB(tmp_path / "pc.db", device="PC")
    blobs, _ = run_sync(WebDAV(dav_server, "me@example.com", "app-pass"), "PC", None, {})
    for blob in blobs.values():
        merge_payload(pc, decode(blob))
    assert {r["game"]: r["total"] for r in pc.game_stats()} == {"Monster Hunter Freedom Unite": 190.0}


def test_deck_names_in_another_language_merge_into_the_pc_name(tmp_path):
    """The Deck's Ryujinx runs in English; the PC already calls the game by its Chinese name."""
    pc = DB(tmp_path / "pc.db", device="PC")
    pc.canonical("塞尔达传说 智慧的再现", "switch:01008cf01baac000")
    store = deck.Store(tmp_path / "s.json", "deck1")
    t = deck.Tracker(store)
    found = ("Ryujinx", "The Legend of Zelda: Echoes of Wisdom", "switch:01008cf01baac000")
    for s in range(0, 130, 10):
        t.tick(1_800_000_000 + s, found)
    t.tick(1_800_000_130, ("Ryujinx", "<Unknown Name>", "switch:01008cf01baac000"))   # same id, no name: same game
    t.tick(1_800_000_140, None)
    store.import_totals({"ryujinx": [("�garbled", 900.0, 1.0, "switch:01007ef00011e000", "Ryujinx")]})
    assert [s["game"] for s in store.sessions] == ["The Legend of Zelda: Echoes of Wisdom"]   # garbled id: skipped
    merge_payload(pc, decode(deck.encode(store.payload("Steam Deck"))))
    assert pc.games() == ["塞尔达传说 智慧的再现"] and pc.game_detail("塞尔达传说 智慧的再现")["total"] == 130


# --------------------------------------------------------------------------- Steam shortcuts (EmuDeck)

def binary_vdf(d: dict) -> bytes:
    """Writer for Steam's binary KeyValues (as in shortcuts.vdf), to build fixtures."""
    import struct
    out = b""
    for k, v in d.items():
        key = k.encode() + b"\0"
        if isinstance(v, dict):
            out += b"\x00" + key + binary_vdf(v) + b"\x08"
        elif isinstance(v, int):
            out += b"\x02" + key + struct.pack("<i", v)
        else:
            out += b"\x01" + key + v.encode() + b"\0"
    return out


SRM = "/home/deck/Emulation/tools/launchers"
ZELDA_ID = -1234567890          # Steam stores shortcut app ids as signed 32-bit
ZELDA_APPID = ZELDA_ID & 0xFFFFFFFF


@pytest.fixture
def steam(tmp_path, monkeypatch):
    monkeypatch.setattr(deck, "HOME", tmp_path)
    cfg = tmp_path / ".local" / "share" / "Steam" / "userdata" / "291012910" / "config"
    cfg.mkdir(parents=True)
    (cfg / "shortcuts.vdf").write_bytes(binary_vdf({"shortcuts": {
        "0": {"appid": ZELDA_ID, "AppName": "The Legend of Zelda: Echoes of Wisdom", "Exe": f'"{SRM}/ryujinx.sh"',
              "LaunchOptions": "'/run/media/mmcblk0p1/Emulation/roms/switch/Zelda EoW [01008CF01BAAC000][v0].nsp'",
              "LastPlayTime": 1_800_000_000},
        "1": {"appid": 987654, "AppName": "Pokemon Platinum", "Exe": f'"{SRM}/melonds.sh"',
              "LaunchOptions": "'/home/deck/Emulation/roms/nds/pokemon.nds'"},
        "2": {"appid": 555, "AppName": "Magpie", "Exe": '"/home/deck/Magpie.exe"', "LaunchOptions": ""},
    }}) + b"\x08")
    gid = (ZELDA_APPID << 32) | 0x02000000     # what Steam puts in SteamGameId for a shortcut
    (cfg / "localconfig.vdf").write_text(f'''"UserLocalConfigStore" {{ "Software" {{ "Valve" {{ "Steam" {{ "apps" {{
        "{gid}" {{ "LastPlayed" "1800000100" "Playtime" "70" }}
        "987654" {{ "Playtime" "30" }}
        "555" {{ "Playtime" "999" }}
    }} }} }} }} }}''', encoding="utf-8")
    return gid


def test_shortcuts_and_steam_play_time(steam):
    scs = deck.shortcuts()
    assert scs[ZELDA_APPID]["name"] == "The Legend of Zelda: Echoes of Wisdom" and len(scs) == 3
    assert deck.shortcut_playtimes()[ZELDA_APPID] == (4200.0, 1_800_000_100)
    totals = sorted(deck.shortcut_totals())
    assert totals == [("Pokemon Platinum", 1800.0, None, "steamshortcut:987654", "melonDS"),
                      ("The Legend of Zelda: Echoes of Wisdom", 4200.0, 1_800_000_100, "switch:01008cf01baac000", "Ryujinx")]
    # Magpie is not an emulator shortcut: its 999 minutes are not imported


def test_running_game_named_from_its_steam_shortcut(steam):
    d = deck.Detector()
    proc = (31, "Ryujinx", ["/tmp/.mount_x/usr/bin/Ryujinx", "/run/media/mmcblk0p1/Emulation/roms/switch/zelda.nsp"],
            {"SteamGameId": str(steam), "SteamAppId": "0"})
    assert d.detect([proc]) == ("Ryujinx", "The Legend of Zelda: Echoes of Wisdom", "switch:01008cf01baac000")
    melon = (32, "melonDS", ["/app/bin/melonDS", "/home/deck/Emulation/roms/nds/pokemon.nds"], {"SteamGameId": "987654"})
    assert deck.Detector().detect([melon]) == ("melonDS", "Pokemon Platinum", "steamshortcut:987654")
    outside = (33, "melonDS", ["/app/bin/melonDS", "/home/deck/Emulation/roms/nds/pokemon.nds"], {})
    assert deck.Detector().detect([outside]) == ("melonDS", "pokemon", None)          # not from Steam: ROM name
    signed = (34, "Ryujinx", ["/tmp/Ryujinx"], {"SteamGameId": str(steam - (1 << 64))})   # same id, printed signed
    assert deck.Detector().detect([signed])[1] == "The Legend of Zelda: Echoes of Wisdom"



# --------------------------------------------------------------------------- Ryujinx save sync on the Deck


def test_deck_save_sync_follows_the_game(tmp_path, dav_server, monkeypatch, capsys):
    from test_savesync import V1, V2, V3, ZELDA, content, play, ryujinx
    from playclock.savesync import DavStorage, SaveSync
    monkeypatch.setattr(deck, "HOME", tmp_path / "home")
    monkeypatch.setattr(deck, "CONFIG_DIR", tmp_path / "home" / ".config" / "playclock")
    monkeypatch.setattr(deck, "ryujinx_pids", lambda procs=None: [])
    droot = ryujinx(tmp_path / "home" / ".config" / "Ryujinx", {"0000000000000002": (ZELDA, V1)})
    proot = ryujinx(tmp_path / "pc" / "Ryujinx", {"0000000000000004": (ZELDA, V1)})
    pc = SaveSync(proot, DavStorage(dav_server, "me@example.com", "app-pass"), tmp_path / "pc" / "s.json",
                  tmp_path / "pc" / "b", device="pc000000", device_name="RHINO")
    assert pc.sync() == {ZELDA: "upload"}
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device_id": "deck1", "name": "Steam Deck", "save_sync": True,
                               "webdav": {"url": dav_server, "user": "me@example.com", "password": "app-pass"}}))
    agent = deck.Agent(cfg, tmp_path / "data")
    assert agent.saves_enabled() and agent.sync_saves() == {ZELDA: "synced"}

    reader = deck.RyujinxLog([])
    reader.game, reader.key = "Zelda", f"switch:{ZELDA.lower()}"
    agent.detector.ryujinx_logs = {"logs": reader}
    agent.detector.seen = [("Ryujinx", 7, ["Ryujinx"])]
    assert agent.watch_saves(False)                                  # opened: the device file says so
    assert agent.store.payload("Deck", agent.open_game)["open_game"]["id"] == ZELDA
    agent.watch_saves(False)
    assert pc.head(ZELDA)[0]["device_name"] == "RHINO"               # unchanged save: nothing sent
    play(droot, "0000000000000002", V2)                               # an in-game save
    assert agent.watch_saves(False)                                   # -> device file now, so the PC knows
    assert pc.head(ZELDA)[0]["device_name"] == "Steam Deck"          # the first change goes up at once
    assert agent.store.payload("Deck", agent.open_game)["open_game"]["saved"]
    play(droot, "0000000000000002", V3)
    agent.watch_saves(False)                                          # within 15 minutes: waits
    assert pc.head(ZELDA)[0]["hash"] != pc.local_hash(pc.local()[ZELDA])
    agent.last_save_upload -= deck.SAVE_EVERY                         # ... 15 minutes later
    agent.watch_saves(False)
    assert pc.local_hash(pc.local()[ZELDA]) != pc.head(ZELDA)[0]["hash"]   # (the PC hasn't restored yet)
    assert pc.sync() == {ZELDA: "restore"} and content(proot, "0000000000000004") == V3
    play(droot, "0000000000000002", V1)
    reader.game = None                                                # the game is closed: upload now
    assert agent.watch_saves(False) and agent.open_game is None
    assert pc.sync() == {ZELDA: "restore"} and content(proot, "0000000000000004") == V1

    play(proot, "0000000000000004", V2)                               # back on the PC
    assert pc.sync() == {ZELDA: "upload"}
    agent.detector.seen = []                                          # Ryujinx closed on the Deck: restore
    agent.watch_saves(False)
    assert content(droot, "0000000000000002") == V2

    deck.cmd_saves(agent, ["status"])
    out = capsys.readouterr().out
    assert ZELDA in out and "restore" in out and "cloud: RHINO" in out
