"""PlayClock for Steam Deck — the Decky (Quick Access Menu) panel.

The recording and syncing happen in the `playclock-deck` user service (deck/playclock_deck.py), which keeps working
in Desktop Mode and when Decky is broken. This backend only reads the service's status files, edits its settings
(config.json, which the service re-reads by itself), passes requests to it (requests.json + SIGUSR1) and checks a
new cloud account before saving it. It does nothing in the background.
"""
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import decky

HOME = Path(decky.DECKY_USER_HOME)
AGENT_DIR = HOME / ".local" / "share" / "playclock-deck"
CONFIG = HOME / ".config" / "playclock" / "config.json"
SAVE_STATE = HOME / ".config" / "playclock" / "save_sync.json"
DATA = HOME / ".local" / "share" / "playclock"

# The same settings and defaults as the service (playclock_deck.DEFAULTS); only these values can be chosen.
DEFAULTS = {"save_sync": False, "save_every": 15, "launch_check": True, "sleep_upload": True, "quota_mb": 1024}
CHOICES = {"save_sync": (True, False), "save_every": (0, 5, 15, 30), "launch_check": (True, False),
           "sleep_upload": (True, False), "quota_mb": (0, 1024, 2048, 5120)}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_private(path: Path, data) -> None:
    """Atomic, readable by this user only (the file holds the WebDAV app password)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def savesync():
    """The service's savesync.py (game names, the WebDAV check), loaded from where install.sh put it."""
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    import savesync as module  # noqa: PLC0415
    return module


def provider(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return "坚果云" if host.endswith("jianguoyun.com") else f"WebDAV · {host}"


def agent_pid() -> int | None:
    """The running service (its live.json names its pid; checked against /proc)."""
    pid = read_json(DATA / "live.json", {}).get("pid")
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes() if pid else b""
    except OSError:
        return None
    return pid if b"playclock_deck.py" in cmd else None


def poke() -> bool:
    """Wake the service so it acts now instead of at its next 10-second poll."""
    pid = agent_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGUSR1)
            return True
        except OSError:
            pass
    return False


class Plugin:
    async def _main(self):
        decky.logger.info("PlayClock panel loaded")

    async def _unload(self):
        pass

    # --- what the panel shows -----------------------------------------------------
    async def get_state(self) -> dict:
        if not (AGENT_DIR / "playclock_deck.py").is_file():
            return {"installed": False}
        cfg = read_json(CONFIG, {})
        w = cfg.get("webdav") or {}
        saves = read_json(SAVE_STATE, {})
        month = time.strftime("%Y-%m")
        traffic = saves.get("traffic") or {}
        names = {}
        try:
            root = next((d for d in (HOME / ".config" / "Ryujinx",) if (d / "games").is_dir()), None)
            names = savesync().game_names(root) if root else {}
        except Exception:  # names are a nicety
            decky.logger.exception("game names")
        games = []
        for title, g in sorted(saves.get("games", {}).items()):
            if g.get("status") == "needs_local":
                continue  # a game this Deck doesn't have: not shown
            head = g.get("head") or {}
            games.append({
                "title": title, "name": names.get(title) or head.get("game") or title, "status": g.get("status", ""),
                "conflict": g.get("status") == "conflict" or bool(g.get("remote_conflicts")),
                "mine": bool((g.get("conflict") or {}).get("file")),
                "cloud_device": head.get("device_name", ""), "cloud_time": head.get("time"),
                "restored_from": g.get("restored_from", ""),
            })
        return {
            "installed": True, "running": agent_pid() is not None,
            "live": read_json(DATA / "live.json", {}),
            "name": cfg.get("name", ""),
            "cloud": {"provider": provider(w["url"]), "url": w["url"], "user": w["user"]} if w else None,
            "settings": {k: cfg.get(k, v) for k, v in DEFAULTS.items()},
            "games": games,
            "traffic": {"up": traffic.get("up", 0), "down": traffic.get("down", 0)} if traffic.get("month") == month
            else {"up": 0, "down": 0},
        }

    # --- changes --------------------------------------------------------------------
    async def set_setting(self, key: str, value) -> dict:
        if key not in CHOICES or value not in CHOICES[key]:
            raise ValueError(f"{key}={value!r}")
        cfg = read_json(CONFIG, {})
        cfg[key] = value
        write_private(CONFIG, cfg)
        poke()
        return await self.get_state()

    async def set_name(self, name: str) -> dict:
        name = name.strip()[:40]
        if not name:
            raise ValueError("empty name")
        cfg = read_json(CONFIG, {})
        cfg["name"] = name
        write_private(CONFIG, cfg)
        poke()
        return await self.get_state()

    async def set_account(self, url: str, user: str, password: str) -> dict:
        """Check the account against the server first; save only if it works. An empty password keeps the
        current one (to change just the server or the account name)."""
        cfg = read_json(CONFIG, {})
        old = cfg.get("webdav") or {}
        url, user = (url or "").strip() or "https://dav.jianguoyun.com/dav/", (user or "").strip()
        password = password or (old.get("password", "") if user == old.get("user") else "")
        if not user or not password:
            return {"ok": False, "message": "请填写账号和应用密码"}
        try:
            savesync().DavStorage(url, user, password, timeout=15, agent="PlayClock-Decky").check()
        except Exception as e:  # StorageError("auth") / network / bad URL
            msg = str(e)
            return {"ok": False, "message": "账号或应用密码不正确" if msg == "auth" else f"连接失败：{msg}"}
        cfg["webdav"] = {"url": url, "user": user, "password": password}
        write_private(CONFIG, cfg)
        poke()
        decky.logger.info(f"cloud account changed to {user} @ {provider(url)}")
        return {"ok": True, "message": "已连接"}

    async def get_versions(self, title: str) -> dict:
        """A game's cloud versions and local backups, newest first. Listing the cloud needs xml.etree, which
        Decky's bundled Python lacks, so the service's own command does it with the system Python."""
        if not title.isalnum():
            raise ValueError(title)
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/python3", str(AGENT_DIR / "playclock_deck.py"), "saves", "versions", title,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "message": "读取云端超时"}
        if proc.returncode != 0:
            return {"ok": False, "message": (err.decode("utf-8", "replace").strip().splitlines() or ["失败"])[-1]}
        data = json.loads(out.decode("utf-8"))
        return {"ok": True, **data}

    async def request(self, action: str, title: str = "", keep: str = "", version: str = "") -> dict:
        """sync (play records + saves now) / sync_game (one game) / resolve a save conflict (keep "cloud" or
        "local") / restore_version (a cloud version file or a local backup path) — done by the service."""
        if (action not in ("sync", "sync_game", "resolve", "restore_version")
                or (action == "resolve" and keep not in ("cloud", "local"))
                or (action == "restore_version" and not version)):
            raise ValueError(action)
        path = DATA / "requests.json"
        pending = read_json(path, [])
        pending = pending if isinstance(pending, list) else []
        pending.append({"action": action, "title": title, "keep": keep, "version": version, "at": time.time()})
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pending), encoding="utf-8")
        os.replace(tmp, path)
        return {"ok": True, "woke": poke()}
