"""Ryujinx save sync (playclock/savesync.py): two fake Ryujinx installs ("PC" and "Deck", different save
indexes for the same game) talking through a real local WebDAV server."""
import json
import os
import threading
from pathlib import Path

import pytest

from playclock import savesync as ss
from playclock.savesync import DavStorage, SaveSync, decide, files_hash, pack, parse_extra, unpack

USER = "00000000000000010000000000000000"
ZELDA, MARIO = "01008CF01BAAC000", "0100000000010000"

# The first 0x30 bytes of ExtraData0 of save 0000000000000001 on the user's PC (read 2026-10-17).
REAL_EXTRA = bytes.fromhex(
    "00a0120230950001" "0100000000000000" "0000000000000000" "0000000000000000"
    "0100000000000000" "0000000000000000")


def extra(title: str, user: str = USER, typ: int = 1) -> bytes:
    lo, hi = int(user[:16], 16), int(user[16:], 16)
    data = bytearray(0x200)
    data[0:8] = int(title, 16).to_bytes(8, "little")
    data[8:16] = lo.to_bytes(8, "little")
    data[16:24] = hi.to_bytes(8, "little")
    data[0x20] = typ
    data[0x40:0x48] = int(title, 16).to_bytes(8, "little")
    return bytes(data)


def ryujinx(root: Path, saves: dict[str, tuple[str, dict[str, bytes]]], types: dict[str, int] | None = None) -> Path:
    """saves: index -> (title, files). Writes 0/ and 1/ with the same files, like Ryujinx."""
    for index, (title, files) in saves.items():
        d = root / "bis" / "user" / "save" / index
        for sub in ("0", "1"):
            (d / sub).mkdir(parents=True, exist_ok=True)
            for rel, data in files.items():
                p = d / sub / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        (d / "ExtraData0").write_bytes(extra(title, typ=(types or {}).get(index, 1)))
        (d / "ExtraData1").write_bytes(extra(title, typ=(types or {}).get(index, 1)))
    (root / "system").mkdir(parents=True, exist_ok=True)
    (root / "system" / "Profiles.json").write_text(json.dumps(
        {"profiles": [{"user_id": USER, "name": "RyuPlayer", "account_state": "Open"}]}))
    return root


def play(root: Path, index: str, files: dict[str, bytes]) -> None:
    """The game saves: Ryujinx rewrites 0/ (and 1/)."""
    for sub in ("0", "1"):
        d = root / "bis" / "user" / "save" / index / sub
        for p in sorted(d.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        for rel, data in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_bytes(data)
    os.utime(root / "bis" / "user" / "save" / index / "0")


def content(root: Path, index: str, sub: str = "0") -> dict[str, bytes]:
    return ss.read_tree(root / "bis" / "user" / "save" / index / sub)


@pytest.fixture
def dav(tmp_path):
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    root = tmp_path / "dav"
    (root / "PlayClock").mkdir(parents=True)
    (root / "PlayClock" / "keys.txt").write_text("the user's own file")
    app = WsgiDAVApp({
        "provider_mapping": {"/dav": str(root)},
        "http_authenticator": {"domain_controller": None, "accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "simple_dc": {"user_mapping": {"*": {"me@example.com": {"password": "app-pass"}}}},
        "verbose": 0, "logging": {"enable": False},
    })
    server = wsgi.Server(("127.0.0.1", 0), app)
    server.prepare()
    threading.Thread(target=server.serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.bind_addr[1]}/dav/", root
    server.stop()


def device(tmp_path, dav, name, saves, busy=lambda: False, **kw) -> SaveSync:
    url, _ = dav
    root = ryujinx(tmp_path / name / "Ryujinx", saves, kw.pop("types", None))
    storage = DavStorage(url, "me@example.com", "app-pass")
    return SaveSync(root, storage, tmp_path / name / "state.json", tmp_path / name / "backups",
                    device=name.lower() * 8, device_name=name, busy=busy, **kw)


V1 = {"DATA0.DAT": b"progress 1" * 100}
V2 = {"DATA0.DAT": b"progress 2" * 100, "album/000_Photo.jpg": b"\xff\xd8 photo"}
V3 = {"DATA0.DAT": b"progress 3" * 100}


# --------------------------------------------------------------------------- format


def test_extra_data_from_the_users_pc():
    assert parse_extra(REAL_EXTRA) == ("010095300212A000", USER, 1)
    assert parse_extra(extra(ZELDA)) == (ZELDA, USER, 1)


def test_hash_ignores_order_and_zip_round_trips():
    files = {"b/x.bin": b"1", "a.dat": b"2"}
    assert files_hash(files) == files_hash(dict(reversed(list(files.items()))))
    assert files_hash(files) != files_hash({"a.dat": b"2", "b/x.bin": b"3"})
    got, meta = unpack(pack(files, {"hash": "h"}))
    assert got == files and meta == {"hash": "h"}


def test_unsafe_zip_paths_are_refused():
    import io
    import zipfile
    for bad in ("save/../../evil", "save//x", "../x", "save/C:/x", "meta/x"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(bad, b"x")
        with pytest.raises(ValueError):
            unpack(buf.getvalue())


def test_decision_table():
    head = {"hash": "R", "supersedes": ["OLD"]}
    assert decide("L", None, None) == "upload"
    assert decide(None, None, None) == "none"
    assert decide(None, "B", head) == "needs_local"
    assert decide("R", "B", head) == "synced"
    assert decide("B", "B", head) == "restore"          # only the cloud changed
    assert decide("L", "R2", {"hash": "R2"}) == "upload"  # only this device changed
    assert decide("L", "B", head) == "conflict"          # both changed
    assert decide("OLD", "B", head) == "restore"         # a user picked the cloud's version over ours
    assert decide("L", None, head) == "conflict"         # first time on this device, different content


# --------------------------------------------------------------------------- two devices


def test_pc_to_deck_lands_in_a_different_index(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V2)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000001": (ZELDA, V1)})
    assert pc.sync() == {ZELDA: "upload"}
    # the Deck's save is a different one with no common history: conflict, nothing local touched
    assert deck.sync() == {ZELDA: "conflict"}
    assert content(deck.root, "0000000000000001") == V1
    assert len(deck.conflicts(ZELDA)) == 1 and deck.backups(ZELDA)
    # the user keeps the PC's (cloud) version, from the PC
    pc.resolve(ZELDA, "head")
    assert deck.conflicts(ZELDA) == []
    assert deck.sync() == {ZELDA: "restore"}
    for sub in ("0", "1"):
        assert content(deck.root, "0000000000000001", sub) == V2
    assert parse_extra((deck.root / "bis/user/save/0000000000000001/ExtraData0").read_bytes())[0] == ZELDA
    assert deck.sync() == {ZELDA: "synced"} and pc.sync() == {ZELDA: "synced"}


def test_play_on_one_side_flows_to_the_other(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1)})
    assert pc.sync() == {ZELDA: "upload"}
    assert deck.sync() == {ZELDA: "synced"}              # same content: nothing to move
    play(deck.root, "0000000000000002", V2)
    assert deck.sync() == {ZELDA: "upload"}
    assert pc.sync() == {ZELDA: "restore"} and content(pc.root, "0000000000000004") == V2
    play(pc.root, "0000000000000004", V3)
    assert pc.sync() == {ZELDA: "upload"}
    assert deck.sync() == {ZELDA: "restore"} and content(deck.root, "0000000000000002") == V3


def test_conflict_keeping_the_other_devices_version(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1)})
    pc.sync(), deck.sync()
    play(pc.root, "0000000000000004", V2)
    play(deck.root, "0000000000000002", V3)               # the Deck was asleep with the game open
    assert pc.sync() == {ZELDA: "upload"}
    assert deck.sync() == {ZELDA: "conflict"}
    assert deck.sync() == {ZELDA: "conflict"} and len(pc.conflicts(ZELDA)) == 1   # uploaded once
    pc.resolve(ZELDA, pc.conflicts(ZELDA)[0]["file"])    # the user picks the Deck's progress, on the PC
    assert pc.sync() == {ZELDA: "restore"} and content(pc.root, "0000000000000004") == V3
    assert deck.sync() == {ZELDA: "synced"} and content(deck.root, "0000000000000002") == V3


def test_restore_waits_while_a_game_runs(tmp_path, dav):
    running = [True]
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1)}, busy=lambda: running[0])
    pc.sync(), deck.sync()
    play(pc.root, "0000000000000004", V2)
    pc.sync()
    assert deck.sync() == {ZELDA: "pending_restore"} and content(deck.root, "0000000000000002") == V1
    running[0] = False
    assert deck.sync() == {ZELDA: "restore"} and content(deck.root, "0000000000000002") == V2


def test_game_without_a_local_save_and_other_save_types(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1), "0000000000000005": (MARIO, V1)},
                types={"0000000000000005": 2})          # Mario's BCAT save: not synced
    deck = device(tmp_path, dav, "Deck", {})
    assert pc.sync() == {ZELDA: "upload"}
    assert deck.sync() == {ZELDA: "needs_local"}


def test_old_versions_pruned_and_nothing_else_touched(tmp_path, dav):
    _, root = dav
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    for i in range(8):
        play(pc.root, "0000000000000004", {"DATA0.DAT": f"step {i}".encode()})
        assert pc.sync() == {ZELDA: "upload"}
    folder = root / "PlayClock" / "saves" / "ryujinx" / ZELDA
    versions = sorted(p.name for p in folder.glob("v-*.zip"))
    assert len(versions) == ss.KEEP_VERSIONS
    head = json.loads((folder / "head.json").read_text(encoding="utf-8"))
    assert head["file"] == versions[-1] and head["device_name"] == "PC"
    assert (root / "PlayClock" / "keys.txt").read_text() == "the user's own file"


def test_failed_swap_rolls_back(tmp_path, dav, monkeypatch):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1)})
    pc.sync(), deck.sync()
    play(pc.root, "0000000000000004", V2)
    pc.sync()
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append((a, b))
        if len(calls) == 3:   # 0 swapped, then failing while swapping 1
            raise OSError("disk full")
        return real(a, b)

    monkeypatch.setattr(ss.os, "replace", flaky)
    with pytest.raises(OSError):
        deck.sync()
    monkeypatch.setattr(ss.os, "replace", real)
    for sub in ("0", "1"):
        assert content(deck.root, "0000000000000002", sub) == V1
    assert deck.sync() == {ZELDA: "restore"} and content(deck.root, "0000000000000002") == V2


def test_history_restore_then_others_follow(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1)})
    pc.sync(), deck.sync()
    play(pc.root, "0000000000000004", V2)
    pc.sync()
    old = pc.versions(ZELDA)[-1]                           # the first upload (V1)
    pc.restore_version(ZELDA, old)
    assert content(pc.root, "0000000000000004") == V1 and pc.backups(ZELDA)
    assert pc.sync() == {ZELDA: "upload"}
    deck.sync()
    assert content(deck.root, "0000000000000002") == V1
    pc.restore_version(ZELDA, pc.backups(ZELDA)[0])        # undo: the backup taken just before going back to V1
    assert content(pc.root, "0000000000000004") == V2


def test_upload_only_when_changed_and_traffic_counted(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1)}, quota_up=1_000)
    assert pc.upload_if_changed(ZELDA)                            # first time: uploads
    assert not pc.upload_if_changed(ZELDA)                        # unchanged
    play(pc.root, "0000000000000004", V2)
    assert pc.upload_if_changed(ZELDA)
    up, down = pc.traffic()
    assert up > 0 and down > 0
    assert pc.degraded()                                          # tiny test quota: over 70 %
    saved = json.loads(pc.state_path.read_text(encoding="utf-8"))
    assert saved["traffic"]["up"] == up


def test_garbled_ryujinx_titles_are_not_names(tmp_path):
    for tid, title in (("01007EF00011E000", "E��J`"), ("0100000000010000", "<Unknown Name>"),
                       ("01008CF01BAAC000", "塞尔达传说 智慧的再现")):
        meta = tmp_path / "games" / tid.lower() / "gui" / "metadata.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({"title": title}), encoding="utf-8")
    assert ss.game_names(tmp_path) == {"01008CF01BAAC000": "塞尔达传说 智慧的再现"}


def test_quick_check_before_a_launch(tmp_path, dav):
    pc = device(tmp_path, dav, "PC", {"0000000000000004": (ZELDA, V1), "0000000000000005": (MARIO, V1)})
    deck = device(tmp_path, dav, "Deck", {"0000000000000002": (ZELDA, V1), "0000000000000003": (MARIO, V1)})
    pc.sync(), deck.sync()
    play(pc.root, "0000000000000004", V2)
    pc.sync()
    assert deck.sync(quick=True) == {ZELDA: "restore", MARIO: "synced"}
    assert content(deck.root, "0000000000000002") == V2
