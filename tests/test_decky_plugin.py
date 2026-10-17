"""The Decky panel's backend (deck/decky/main.py) against a fake Deck home: state, settings, account, requests."""
import asyncio
import importlib.util
import json
import logging
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    fake = types.ModuleType("decky")
    fake.DECKY_USER_HOME = str(tmp_path)
    fake.logger = logging.getLogger("decky-test")
    monkeypatch.setitem(sys.modules, "decky", fake)
    agent_dir = tmp_path / ".local" / "share" / "playclock-deck"
    agent_dir.mkdir(parents=True)
    shutil.copy(ROOT / "deck" / "playclock_deck.py", agent_dir)
    shutil.copy(ROOT / "playclock" / "savesync.py", agent_dir)
    monkeypatch.delitem(sys.modules, "savesync", raising=False)
    spec = importlib.util.spec_from_file_location("playclock_decky_main", ROOT / "deck" / "decky" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod, tmp_path
    sys.path[:] = [p for p in sys.path if p != str(agent_dir)]
    sys.modules.pop("savesync", None)


@pytest.fixture
def dav(tmp_path):
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    (tmp_path / "dav").mkdir()
    server = wsgi.Server(("127.0.0.1", 0), WsgiDAVApp({
        "provider_mapping": {"/dav": str(tmp_path / "dav")},
        "http_authenticator": {"domain_controller": None, "accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "simple_dc": {"user_mapping": {"*": {"me@example.com": {"password": "app-pass"}}}},
        "verbose": 0, "logging": {"enable": False}}))
    server.prepare()
    threading.Thread(target=server.serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.bind_addr[1]}/dav/"
    server.stop()


def run(coro):
    return asyncio.run(coro)


def test_panel_backend(plugin, dav):
    mod, home = plugin
    p = mod.Plugin()
    config = home / ".config" / "playclock" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"device_id": "deck1", "name": "steamdeck",
                                  "webdav": {"url": "https://dav.jianguoyun.com/dav/", "user": "a@b.c", "password": "x"}}))
    (home / ".config" / "playclock" / "save_sync.json").write_text(json.dumps({"games": {
        "01000E200DC58000": {"status": "conflict", "conflict": {"file": "conflict-x.zip"}, "head": {"device_name": "RHINO"}},
        "0100000000010000": {"status": "synced", "head": {"device_name": "steamdeck", "time": 1, "game": "Odyssey"}}}}))

    state = run(p.get_state())
    assert state["installed"] and not state["running"]           # no live.json / service in a test
    assert state["cloud"] == {"provider": "坚果云", "url": "https://dav.jianguoyun.com/dav/", "user": "a@b.c"}
    assert "password" not in json.dumps(state)
    assert state["settings"]["save_every"] == 15 and state["settings"]["save_sync"] is False
    games = {g["title"]: g for g in state["games"]}
    assert games["01000E200DC58000"]["conflict"] and games["01000E200DC58000"]["mine"]
    assert games["0100000000010000"]["name"] == "Odyssey"

    state = run(p.set_setting("save_every", 5))
    assert state["settings"]["save_every"] == 5
    with pytest.raises(ValueError):
        run(p.set_setting("save_every", 7))                         # only the offered choices
    with pytest.raises(ValueError):
        run(p.set_setting("device_id", "x"))
    assert run(p.set_name("  Deck OLED "))["name"] == "Deck OLED"
    assert (config.stat().st_mode & 0o777) == 0o600 or sys.platform == "win32"

    # account: checked against the server before it is saved; the old one stays on failure
    assert run(p.set_account(dav, "me@example.com", "wrong")) == {"ok": False, "message": "账号或应用密码不正确"}
    assert json.loads(config.read_text())["webdav"]["user"] == "a@b.c"
    assert run(p.set_account(dav, "me@example.com", "app-pass"))["ok"]
    saved = json.loads(config.read_text())
    assert saved["webdav"] == {"url": dav, "user": "me@example.com", "password": "app-pass"}
    assert saved["device_id"] == "deck1" and saved["save_every"] == 5
    assert run(p.set_account(dav + "?", "me@example.com", ""))["ok"]    # empty password = keep the current one
    assert run(p.get_state())["cloud"]["provider"].startswith("WebDAV · 127.0.0.1")

    # requests queue up for the service
    run(p.request("sync"))
    run(p.request("resolve", "01000E200DC58000", "cloud"))
    queued = json.loads((home / ".local" / "share" / "playclock" / "requests.json").read_text())
    assert [(r["action"], r["keep"]) for r in queued] == [("sync", ""), ("resolve", "cloud")]
    with pytest.raises(ValueError):
        run(p.request("resolve", "01000E200DC58000", "both"))
    with pytest.raises(ValueError):
        run(p.request("restore_version", "01000E200DC58000"))           # which version?
    run(p.request("sync_game", "01000E200DC58000"))
    run(p.request("restore_version", "01000E200DC58000", "", "v-x.zip"))
    queued = json.loads((home / ".local" / "share" / "playclock" / "requests.json").read_text())
    assert [(r["action"], r["version"]) for r in queued[-2:]] == [("sync_game", ""), ("restore_version", "v-x.zip")]
    with pytest.raises(ValueError):
        run(p.get_versions("../etc"))


def test_panel_without_the_service(plugin):
    mod, home = plugin
    (home / ".local" / "share" / "playclock-deck" / "playclock_deck.py").unlink()
    assert run(mod.Plugin().get_state()) == {"installed": False}
