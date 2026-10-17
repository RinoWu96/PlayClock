"""When the PC looks for newer saves after another device synced (Controller.sync_finished)."""
import os

import pytest

pytest.importorskip("PySide6")


def test_pc_checks_saves_when_the_deck_closes_a_game_or_reports_an_in_game_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import playclock.app as A
    from playclock.sync import encode
    for name, value in (("DATA", tmp_path), ("COVERS", tmp_path / "covers"), ("CONFIG_PATH", tmp_path / "c.json"),
                        ("SAVE_STATE", tmp_path / "s.json"), ("SAVE_BACKUPS", tmp_path / "b")):
        monkeypatch.setattr(A, name, value)
    monkeypatch.setattr(A.QTimer, "singleShot", staticmethod(lambda _ms, fn: fn()))  # run at once
    app = QApplication.instance() or QApplication([])
    cfg = dict(A.DEFAULT_CONFIG, device_id="pc", language="zh")
    db = A.DB(tmp_path / "p.db", device="pc")
    ctrl = A.Controller(db, A.Tracker(db, A.load_rules(), cfg), cfg)
    checks = []
    ctrl.sync_saves = lambda *a, **k: checks.append(1)

    def deck_file(open_game):
        payload = {"format": 1, "device": "deck", "name": "Deck", "sessions": [], "aliases": [], "tombstones": [],
                   "keys": [], "open_game": open_game}
        ctrl.sync_finished(({"deck.json.gz": encode(payload)}, {"deck.json.gz": os.urandom(4).hex()}), None,
                           None, False)
        return len(checks)

    assert deck_file({"id": "01008CF01BAAC000", "since": 1}) == 0          # playing, nothing saved yet
    assert deck_file({"id": "01008CF01BAAC000", "since": 1}) == 0          # still playing (play time grew)
    assert deck_file({"id": "01008CF01BAAC000", "since": 1, "saved": 5}) == 1   # an in-game save went up
    assert deck_file({"id": "01008CF01BAAC000", "since": 1, "saved": 5}) == 1   # nothing new
    assert deck_file(None) == 2                                                 # the game was closed
    assert ctrl.remote_open["Deck"] == {}
    assert app is not None
