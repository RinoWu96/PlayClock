import threading

import pytest

from playclock.db import DB
from playclock.importers import EmuGame, apply_import, plan_import
from playclock.sync import (
    SyncError, WebDAV, decode, encode, export_payload, live_devices, merge_payload, protect, run_sync, unprotect,
)


def two(tmp_path):
    return DB(tmp_path / "a.db", device="A"), DB(tmp_path / "b.db", device="B")


def exchange(x: DB, y: DB):
    merge_payload(y, decode(encode(export_payload(x, "x"))))
    merge_payload(x, decode(encode(export_payload(y, "y"))))


def totals(db):
    return {r["game"]: r["total"] for r in db.game_stats()}


def test_sessions_flow_both_ways_and_updates_win(tmp_path):
    a, b = two(tmp_path)
    sa = a.start_session("Zelda", "Ryujinx", 100)
    a.update_session(sa, 200, 100)
    sb = b.start_session("Mario", "Ryujinx", 300)
    b.update_session(sb, 400, 60)
    exchange(a, b)
    assert totals(a) == totals(b) == {"Zelda": 100, "Mario": 60}
    a.update_session(sa, 900, 800)             # still playing on A
    exchange(a, b)
    assert totals(b)["Zelda"] == 800
    exchange(a, b)                              # idempotent
    assert totals(a) == totals(b) == {"Zelda": 800, "Mario": 60}


def test_deletes_and_renames_propagate(tmp_path):
    a, b = two(tmp_path)
    s1 = a.start_session("Raw Name", "Ryujinx", 0)
    a.update_session(s1, 100, 100)
    s2 = a.start_session("Other", "PCSX2", 0)
    a.update_session(s2, 100, 100)
    exchange(a, b)
    b.rename_game("Raw Name", "Nice Name")      # rename on the device that does NOT own the session
    b.delete_game("Other")
    exchange(a, b)
    assert totals(a) == totals(b) == {"Nice Name": 100}
    assert a.resolve("Raw Name") == "Nice Name"  # future detections on A follow the rename too
    exchange(a, b)                              # A's stale copy must not bring "Other" back
    assert totals(b) == {"Nice Name": 100}


def test_same_steam_import_on_two_devices_is_not_doubled(tmp_path):
    a, b = two(tmp_path)
    g = [EmuGame("Warframe", 3600, 1000)]
    apply_import(a, plan_import(a, g, "Steam"), "Steam", "steam:42")
    apply_import(b, plan_import(b, g, "Steam"), "Steam", "steam:42")
    exchange(a, b)
    assert totals(a) == totals(b) == {"Warframe": 3600}


def test_ryujinx_imports_are_per_device(tmp_path):
    a, b = two(tmp_path)                        # e.g. a PC and a Steam Deck, each with its own Ryujinx
    apply_import(a, plan_import(a, [EmuGame("Zelda", 3600, 1)], "Ryujinx"), "Ryujinx", "ryujinx")
    apply_import(b, plan_import(b, [EmuGame("Zelda", 7200, 2)], "Ryujinx"), "Ryujinx", "ryujinx")
    exchange(a, b)
    assert totals(a) == totals(b) == {"Zelda": 10800}
    # B's tracked time doesn't reduce what A imports from A's own Ryujinx
    sid = b.start_session("Zelda", "Ryujinx", 0)
    b.update_session(sid, 500, 500)
    exchange(a, b)
    assert plan_import(a, [EmuGame("Zelda", 3600, 1)], "Ryujinx")[0].add == 3600


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT NOT NULL,
        emulator TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL, seconds REAL NOT NULL DEFAULT 0, source TEXT);
        CREATE TABLE aliases (raw TEXT PRIMARY KEY, name TEXT NOT NULL);
        INSERT INTO sessions(game, emulator, start, end, seconds) VALUES ('G', 'Ryujinx', 0, 50, 50);""")
    con.commit()
    con.close()
    db = DB(path, device="PC")
    row = db.con.execute("SELECT uid, device, updated FROM sessions").fetchone()
    assert len(row["uid"]) == 32 and row["device"] == "PC" and row["updated"] == 50


def test_removed_devices_are_forgotten():
    names = {"PC": "Desk", "OLD": "Old laptop"}
    assert live_devices(names, {"PC.json.gz": "e1", "NEW.json.gz": "e2"}) == {"PC": "Desk"}


def test_password_is_encrypted_for_this_user():
    token = protect("app-password")
    assert "app-password" not in token and unprotect(token) == "app-password"


# --------------------------------------------------------------------------- real WebDAV round trip


@pytest.fixture
def dav_server(tmp_path):
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    root = tmp_path / "dav"
    root.mkdir()
    app = WsgiDAVApp({
        "provider_mapping": {"/dav": str(root)},
        "http_authenticator": {"domain_controller": None, "accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "simple_dc": {"user_mapping": {"*": {"me@example.com": {"password": "app-pass"}}}},
        "verbose": 0, "logging": {"enable": False},
    })
    server = wsgi.Server(("127.0.0.1", 0), app)
    server.prepare()
    t = threading.Thread(target=server.serve, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.bind_addr[1]}/dav/"
    server.stop()


def test_two_devices_sync_through_webdav(tmp_path, dav_server):
    a, b = two(tmp_path)
    sid = a.start_session("Zelda", "Ryujinx", 0)
    a.update_session(sid, 3600, 3600)
    b.add_manual("Mario Kart", "Switch", 1800, 5000)
    client = WebDAV(dav_server, "me@example.com", "app-pass")
    client.check()

    def sync(db, known):
        blobs, tags = run_sync(client, db.device, encode(export_payload(db, db.device)), known)
        for blob in blobs.values():
            merge_payload(db, decode(blob))
        return tags

    ka = sync(a, {})
    sync(b, {})
    ka = sync(a, ka)
    assert totals(a) == totals(b) == {"Zelda": 3600, "Mario Kart": 1800}
    assert set(ka) == {"A.json.gz", "B.json.gz"}
    blobs, _ = run_sync(client, "A", None, ka)   # nothing changed -> nothing downloaded
    assert blobs == {}

    with pytest.raises(SyncError, match="auth"):
        WebDAV(dav_server, "me@example.com", "wrong").check()


def test_summary_for_viewers(tmp_path):
    import json
    import time
    from playclock.summary import build_summary, content_hash, encode_summary
    db = DB(tmp_path / "a.db", device="PC")
    now = time.time()
    sid = db.start_session("Zelda", "Ryujinx", now - 1800)       # playing right now
    db.update_session(sid, now - 30, 1770)
    old = db.start_session("Mario", "Ryujinx", now - 90000)       # yesterday
    db.update_session(old, now - 86400, 3600)
    apply_import(db, plan_import(db, [EmuGame("Warframe", 360000, now - 10 ** 6)], "Steam"), "Steam", "steam:1")
    platform_of = {"Ryujinx": "Switch"}.get
    s = build_summary(db, lambda e: platform_of(e, e), {"PC": "Desk"}, now)
    assert [(p["game"], p["platform"], p["device"]) for p in s["playing"]] == [("Zelda", "Switch", "Desk")]
    assert s["total"] == 1770 + 3600 + 360000
    assert {g["name"]: g["platforms"] for g in s["games"]} == {"Warframe": ["Steam"], "Mario": ["Switch"], "Zelda": ["Switch"]}
    assert sum(d[1] for d in s["daily"]) == 1770 + 3600            # imported totals have no dates
    assert [r["game"] for r in s["recent"]] == ["Zelda", "Mario"]
    assert s["platforms"][0][0] == "Steam" and s["has_imports"]
    assert json.loads(encode_summary(s)) == s
    later = dict(s, generated=now + 60)
    assert content_hash(later) == content_hash(s)                  # timestamp alone doesn't trigger an upload


def test_summary_is_uploaded_next_to_device_files(tmp_path, dav_server):
    client = WebDAV(dav_server, "me@example.com", "app-pass")
    run_sync(client, "PC", b"x", {}, extra={"summary.json": b'{"format":1}'})
    assert client.get("PlayClock/summary.json") == b'{"format":1}'
    assert set(client.listing("PlayClock/devices")) == {"PC.json.gz"}   # summary is not mistaken for a device


def test_thumbnail_is_a_centre_cropped_jpeg(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage
    from playclock.summary import THUMB_SIZE, make_thumb, thumb_name
    src = QImage(600, 900, QImage.Format_RGB32)                   # tall SteamGridDB-style grid
    src.fill(QColor("red"))
    src.save(str(tmp_path / "abc.png"))
    data = make_thumb(tmp_path / "abc.png")
    assert data[:3] == b"\xff\xd8\xff"
    out = QImage.fromData(data)
    assert (out.width(), out.height()) == THUMB_SIZE
    assert make_thumb(tmp_path / "missing.png") is None
    assert thumb_name("abc.def.png") == "abc.def.jpg"


def test_summary_lists_covers(tmp_path):
    from playclock.summary import build_summary
    db = DB(tmp_path / "a.db", device="PC")
    db.add_manual("Zelda", "Switch", 60, 100)
    db.add_manual("Mario", "Switch", 60, 100)
    s = build_summary(db, lambda e: e, {}, covers={"Zelda": "u1.png"})
    assert {g["name"]: g["cover"] for g in s["games"]} == {"Zelda": "covers/u1.jpg", "Mario": None}


def test_only_missing_thumbnails_are_uploaded_in_batches(tmp_path, dav_server):
    client = WebDAV(dav_server, "me@example.com", "app-pass")
    made = []

    def make(path):
        made.append(path)
        return b"jpeg-" + path.encode()

    files = {f"g{i}.jpg": f"local{i}" for i in range(5)}
    run_sync(client, "PC", b"x", {}, thumbs={"files": files, "make": make, "batch": 3})
    assert len(made) == 3
    run_sync(client, "PC", None, {}, thumbs={"files": files, "make": make, "batch": 3})
    assert len(made) == 5 and len(set(made)) == 5                  # second round only did the rest
    run_sync(client, "PC", None, {}, thumbs={"files": files, "make": make, "batch": 3})
    assert len(made) == 5                                          # nothing left -> nothing made
    assert set(client.listing("PlayClock/covers")) == set(files)
    assert client.get("PlayClock/covers/g4.jpg") == b"jpeg-local4"
    assert set(client.listing("PlayClock/devices")) == {"PC.json.gz"}
