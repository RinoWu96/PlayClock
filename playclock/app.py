"""Entry point. A small always-on Controller (tracker, tray, timer) plus a main window that only exists
while it is open — closing it frees all UI memory, so the background footprint stays minimal."""
import gc
import json
import os
import sys
import threading
import time
import uuid
import winreg
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QLibraryInfo, QObject, QPoint, QSize, Qt, QTimer, QTranslator, Signal
from PySide6.QtGui import QIcon, QImage
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QSystemTrayIcon, QTableWidget, QVBoxLayout, QWidget,
)

from . import __version__, i18n
from .db import DB
from .i18n import fmt_dur, tr
from .rules import ensure_user_file, load_rules, platform_map, write_builtin_reference
from .tracker import Tracker
from .winapi import trim_memory
from .ui import theme
from .ui.widgets import app_icon

APP_NAME = "PlayClock"
APP_TITLE = "PlayClock"
BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DATA = BASE / "data"
COVERS = DATA / "covers"
CONFIG_PATH = DATA / "config.json"
SAVE_STATE = DATA / "save_sync.json"              # Ryujinx save sync: what this PC last agreed with the cloud
SAVE_BACKUPS = DATA / "save_backups" / "ryujinx"  # local copies taken before any save is overwritten
USER_RULES_PATH = DATA / "rules.user.json"
BUILTIN_RULES_PATH = DATA / "rules.builtin.json"  # read-only reference, rewritten every launch
LEGACY_RULES_PATH = DATA / "rules.json"  # v0.1 copied all defaults here; renamed to .bak on start
DEFAULT_CONFIG = {"interval": 5, "min_session": 60, "resume_gap": 180, "foreground_only": False, "language": "auto"}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pyw}" "{BASE / "main.py"}" --minimized'


def get_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, APP_NAME)[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


_qt_translator = None


def apply_language(app: QApplication, lang: str) -> None:
    """Set our strings' language and load Qt's own translation (dialog buttons etc.)."""
    global _qt_translator
    i18n.set_language(lang)
    if _qt_translator:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if lang == "zh":
        t = QTranslator(app)
        if t.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(t)
            _qt_translator = t


# --------------------------------------------------------------------------- background

HOTKEY_ID = 0x5C10  # Ctrl+Alt+P


class HotkeyFilter(QAbstractNativeEventFilter):
    """Delivers WM_HOTKEY (registered for the GUI thread, no window) to a callback."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from .winapi import hotkey_id_of
            if hotkey_id_of(message) == HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0


class _Bridge(QObject):
    done = Signal(object, object)  # result, error


_bridges: set = set()


def run_async(fn, callback):
    """Run blocking `fn` (network) on a worker thread; `callback(result, error)` runs on the GUI thread."""
    bridge = _Bridge()
    _bridges.add(bridge)

    def finish(result, error):
        _bridges.discard(bridge)
        callback(result, error)

    bridge.done.connect(finish)

    def work():
        try:
            bridge.done.emit(fn(), None)
        except Exception as e:  # reported to the UI, never raised in the thread
            bridge.done.emit(None, e)

    threading.Thread(target=work, daemon=True).start()


class Controller(QObject):
    """Everything that runs while PlayClock sits in the tray."""

    def __init__(self, db: DB, tracker: Tracker, cfg: dict):
        super().__init__()
        self.db, self.tracker, self.cfg = db, tracker, cfg
        self.window = None
        self.last_game = None
        self._sgdb = None
        self.cover_tried: set[str] = set()
        self.ticks = 0
        self.overlay = None  # floating panel, only exists while shown
        self.hotkey_ok = False
        self.art_queue: list[tuple[str, str]] = []  # (game, image URL) waiting to be downloaded
        self._art_busy = False
        self.save_syncing = False
        self.ryu_running = False          # a Ryujinx window exists
        self.ryu_game: str | None = None  # title id of the game loaded in Ryujinx (paused included)
        self.remote_open: dict[str, dict] = {}  # device name -> the game its Ryujinx has open (device files)

        self.tray = QSystemTrayIcon(QIcon(app_icon()), self)
        self.tray.activated.connect(
            lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.build_tray_menu()
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(int(self.cfg["interval"] * 1000))
        self.on_tick()
        QTimer.singleShot(4000, self.unify_games)  # one name per game, and no double-counted imports

        self.syncing = False
        self.last_auto_sync = 0.0
        self.sync_timer = QTimer(self)  # cloud sync every 10 min (2 while playing if a phone watches; worker thread)
        self.sync_timer.timeout.connect(self.auto_sync)
        self.sync_timer.start(2 * 60 * 1000)
        QTimer.singleShot(5000, self.sync_now)
        QTimer.singleShot(8000, self.sync_saves)

    def build_tray_menu(self):
        self.menu = QMenu()
        self.menu.addAction(tr("打开主界面"), self.show_window)
        self.overlay_action = self.menu.addAction(tr("游戏浮窗") + "\tCtrl+Alt+P", self.toggle_overlay)
        self.overlay_action.setCheckable(True)
        self.overlay_action.setChecked(self.overlay is not None)
        self.menu.addSeparator()
        self.menu.addAction(tr("退出"), self.quit)
        self.tray.setContextMenu(self.menu)

    def set_cfg(self, key, value):
        self.cfg[key] = value
        save_config(self.cfg)

    def set_interval(self, v):
        self.set_cfg("interval", v)
        self.timer.setInterval(v * 1000)

    def on_tick(self):
        try:
            cur = self.tracker.tick()
        except Exception as e:  # never let one bad poll kill the tracker
            self.tray.setToolTip(f"{APP_TITLE}\n" + tr("检测出错：{e}", e=e))
            return
        game = cur.game if cur else None
        tip = tr("正在游玩：{game}\n本次 {t}", game=game, t=fmt_dur(cur.seconds)) if cur else tr("等待游戏启动")
        self.tray.setToolTip(f"{APP_TITLE}\n{tip}")
        changed = game != self.last_game
        if changed:
            self.tray.setIcon(QIcon(app_icon(active=bool(cur))))
            self.last_game = game
            if game and self.cfg.get("auto_cover", True):
                self.auto_cover(game)
        if self.window:  # the UI reads the DB, so keep it current while visible
            self.tracker.flush()
            self.window.on_tick(changed)
        if self.overlay:
            self.overlay.update()
        known = self.cfg.get("ryujinx_paths", [])
        fresh = [p for p in self.tracker.logs if p not in known]
        if fresh:  # remember where Ryujinx lives so its old logs can be read even when it isn't running
            self.set_cfg("ryujinx_paths", known + fresh)
            self.unify_games()
        self.ticks += 1
        if self.ticks % 12 == 1:  # about once a minute: a stat() call unless Steam's file changed
            self.sync_steam()
        self.watch_ryujinx()

    # --- platforms -------------------------------------------------------------
    def platform_of(self, emulator: str) -> str:
        """Ryujinx -> Switch, PCSX2 -> PS2; Steam and hand-entered platforms stay as they are."""
        return platform_map(self.tracker.rules).get(emulator, emulator)

    # --- Steam -------------------------------------------------------------
    def steam_account(self):
        """(root, account) chosen for automatic sync, or (None, None)."""
        from .steam import accounts, steam_root
        acc_id = self.cfg.get("steam_sync")
        root = steam_root() if acc_id else None
        acc = next((a for a in accounts(root) if a.account_id == acc_id), None) if root else None
        return (root, acc) if acc else (None, None)

    def sync_steam(self, force: bool = False):
        """Re-import Steam playtime when Steam has rewritten its records (it does when you stop playing)."""
        root, acc = self.steam_account()
        if not acc:
            return
        mtime = acc.config.stat().st_mtime
        if not force and mtime == self.cfg.get("steam_mtime"):
            return
        from .importers import apply_import, plan_import
        from .steam import scan_steam
        games, _unknown = scan_steam(root, acc, i18n.LANG, self.db.steam_names())
        apply_import(self.db, plan_import(self.db, games, "Steam"), "Steam", f"steam:{acc.account_id}")
        self.set_cfg("steam_mtime", mtime)
        self.import_art(games)
        self.changed()

    def resolve_steam_names(self, appids: list[int], done):
        """Look up names Steam's local cache lacks (public store API), one request at a time."""
        todo = list(appids)

        def next_one(name=None, appid=None):
            if appid is not None and name:
                self.db.add_steam_name(appid, name)
            if not todo:
                done()
                return
            a = todo.pop(0)
            self.sgdb.steam_app_name(a, i18n.LANG, lambda n, a=a: next_one(n, a))

        next_one()

    def import_art(self, games):
        """Use covers platforms already provide (Steam headers) for games that have none."""
        for g in games:
            name = self.db.resolve(g.title)
            if not g.art or self.db.get_cover(name):
                continue
            if isinstance(g.art, Path):
                self.save_cover(name, QImage(str(g.art)), refresh=False)
            elif all(n != name for n, _ in self.art_queue):
                self.art_queue.append((name, g.art))
        self.changed()
        self._next_art()

    def _next_art(self):
        if not self.art_queue or self._art_busy:
            return
        name, url = self.art_queue.pop(0)
        self._art_busy = True

        def got(data, _err):
            self._art_busy = False
            if data and not self.db.get_cover(name):
                self.save_cover(name, QImage.fromData(data), refresh=not self.art_queue)
            self._next_art()

        self.sgdb.download(url, got)

    def changed(self):
        """Data changed outside the window (cover downloaded, import...)."""
        if self.window:
            self.window.refresh_current()

    # --- covers --------------------------------------------------------------
    def sgdb_key(self) -> str:
        return self.cfg.get("sgdb_key", "")

    @property
    def sgdb(self):
        from .covers import SteamGridDB  # network stack only when covers are used
        if self._sgdb is None:
            self._sgdb = SteamGridDB(self.sgdb_key(), self)
        self._sgdb.key = self.sgdb_key()
        return self._sgdb

    def save_cover(self, game: str, img: QImage, refresh: bool = True) -> bool:
        if img.isNull():
            return False
        if img.width() > 1280:
            img = img.scaledToWidth(1280, Qt.SmoothTransformation)
        COVERS.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.png"
        img.save(str(COVERS / name))
        self.remove_cover_file(game)
        self.db.set_cover(game, name)
        if refresh:
            self.changed()
        return True

    def remove_cover_file(self, game: str):
        old = self.db.get_cover(game)
        if old:
            try:
                (COVERS / old).unlink()
            except OSError:
                pass

    def auto_cover(self, game: str, done=None):
        """Fetch the best-guess cover for a game that has none (once per game per run)."""
        if not self.sgdb_key() or self.db.get_cover(game) or game in self.cover_tried:
            if done:
                done()
            return
        self.cover_tried.add(game)

        def got(data, _err):
            if data and not self.db.get_cover(game):
                self.save_cover(game, QImage.fromData(data))
            if done:
                done()

        self.sgdb.first_cover(game, got)

    def fill_missing_covers(self, parent=None):
        missing = [g for g in self.db.games() if not self.db.get_cover(g)]
        self.cover_tried.difference_update(missing)
        if not missing:
            QMessageBox.information(parent, APP_TITLE, tr("所有游戏都已经有封面了。"))
            return
        before = len(missing)

        def next_one():
            if missing:
                self.auto_cover(missing.pop(0), next_one)
            else:
                still = sum(1 for g in self.db.games() if not self.db.get_cover(g))
                self.tray.showMessage(APP_TITLE, tr("已为 {n} 款游戏补全封面。", n=before - still),
                                      QSystemTrayIcon.Information, 3000)

        next_one()

    # --- window lifecycle ------------------------------------------------------
    # --- cloud sync (WebDAV) -------------------------------------------------
    def sync_enabled(self) -> bool:
        return bool(self.cfg.get("webdav"))

    def auto_sync(self):
        """Every 10 minutes; every 2 while a game runs only when a phone watches summary.json (phone_summary)."""
        live = self.tracker.current and self.cfg.get("phone_summary")
        if live or time.time() - self.last_auto_sync >= 10 * 60 - 5:
            self.sync_now()
        if time.time() - self.cfg.get("save_last", 0) >= 24 * 3600:  # saves: a full check once a day
            self.sync_saves()

    def sync_now(self, manual: bool = False):
        w = self.cfg.get("webdav")
        if manual:
            self.sync_saves(manual=True)
        if not w or self.syncing:
            return
        from .sync import WebDAV, digest, encode, export_payload, run_sync, unprotect  # only when sync is on
        self.last_auto_sync = time.time()
        self.tracker.flush()
        me = os.environ.get("COMPUTERNAME", "PC")
        payload = export_payload(self.db, me)
        h = digest(payload)
        upload = encode(payload) if h != self.cfg.get("sync_hash") else None
        extra, thumbs, sh = {}, None, None
        if self.cfg.get("phone_summary"):  # only for the WeChat mini program (shelved): off by default
            from .summary import NAME, THUMB_BATCH, build_summary, content_hash, encode_summary, make_thumb, thumb_name
            names = dict(self.cfg.get("sync_devices", {}), **{self.db.device: me})
            covers = {g: c for g, c in self.db.covers().items() if (COVERS / c).exists()}
            summary = build_summary(self.db, self.platform_of, names, covers=covers)
            thumbs = {"files": {thumb_name(c): COVERS / c for c in covers.values()}, "make": make_thumb,
                      "batch": THUMB_BATCH}
            sh = content_hash(summary)
            extra = {NAME: encode_summary(summary)} if sh != self.cfg.get("summary_hash") else {}
        try:
            client = WebDAV(w["url"], w["user"], unprotect(w["pass"]))
        except Exception as e:  # e.g. config copied from another Windows user: DPAPI can't decrypt
            self.sync_finished(None, e, h, manual)
            return
        known = dict(self.cfg.get("sync_tags", {}))
        self.syncing = True
        if self.window:
            self.window.refresh_sync_state()
        run_async(lambda: run_sync(client, self.db.device, upload, known, extra, thumbs),
                  lambda result, error: self.sync_finished(result, error, h if upload else None, manual,
                                                           sh if extra else None))

    def sync_finished(self, result, error, uploaded_hash, manual, summary_hash=None):
        QTimer.singleShot(1000, self.release_memory)
        from .sync import SyncError, decode, live_devices, merge_payload
        self.syncing = False
        if error is not None:
            msg = str(error)
            if isinstance(error, SyncError) and msg == "auth":
                msg = tr("坚果云账号或应用密码不正确。")
            elif isinstance(error, SyncError) and msg.startswith("network"):
                msg = tr("网络连接失败，稍后会自动重试。")
            self.set_cfg("sync_error", msg)
        else:
            blobs, tags = result
            devices = live_devices(self.cfg.get("sync_devices", {}), tags)  # drop devices whose file is gone
            changed, saves_moved = 0, False
            for blob in blobs.values():
                try:
                    p = decode(blob)
                except (OSError, ValueError):
                    continue  # a half-written or foreign file: skip it, try again next time
                changed += merge_payload(self.db, p)
                name = p.get("name", "?")
                devices[p.get("device", "?")] = name
                open_game = p.get("open_game") or {}
                # Newer saves may be in the cloud when that device has no game open (it just closed one), or when
                # it reports an in-game upload (open_game.saved changes; the Deck uploads at most every 15 min).
                saves_moved |= not open_game or open_game.get("saved") != self.remote_open.get(name, {}).get("saved")
                self.remote_open[name] = open_game
            self.cfg.update(sync_tags=tags, sync_devices=devices, sync_error="", last_sync=time.time())
            if uploaded_hash:
                self.cfg["sync_hash"] = uploaded_hash
            if summary_hash:
                self.cfg["summary_hash"] = summary_hash
            save_config(self.cfg)
            if changed:
                self.changed()
            if saves_moved:
                QTimer.singleShot(2000, self.sync_saves)
        if self.window:
            self.window.refresh_sync_state()
        if manual:
            self.tray.showMessage(APP_TITLE, self.cfg.get("sync_error") or tr("同步完成。"),
                                  QSystemTrayIcon.Warning if error else QSystemTrayIcon.Information, 3000)

    def set_webdav(self, url: str, user: str, password: str):
        from .sync import protect
        self.cfg.update(webdav={"url": url, "user": user, "pass": protect(password)},
                        sync_tags={}, sync_hash="", summary_hash="", sync_error="")
        save_config(self.cfg)
        self.sync_now(manual=True)

    def clear_webdav(self):
        for k in ("webdav", "sync_tags", "sync_hash", "summary_hash", "sync_error", "last_sync", "sync_devices"):
            self.cfg.pop(k, None)
        save_config(self.cfg)

    # --- Ryujinx save sync (savesync.py, docs/SAVE_SYNC_DESIGN.md) -----------------
    # Like Steam Cloud: a game's save goes up right after it is closed; the cloud's newer saves come down at
    # start-up, when Ryujinx closes, when another device's file changes, daily, and on "Sync now" — only while
    # Ryujinx is closed, after a local backup.
    def save_root(self) -> Path | None:
        """Ryujinx's data folder: portable next to a known Ryujinx.exe, else %APPDATA%\\Ryujinx."""
        cands = [Path(p).parent / "portable" for p in self.cfg.get("ryujinx_paths", [])]
        if os.environ.get("APPDATA"):
            cands.append(Path(os.environ["APPDATA"]) / "Ryujinx")
        return next((c for c in cands if (c / "bis" / "user" / "save").is_dir()), None)

    def save_sync_on(self) -> bool:
        return bool(self.cfg.get("save_sync") and self.cfg.get("webdav") and self.save_root())

    def make_save_sync(self):
        """A SaveSync bound to this PC (call on the worker thread: it may hash files)."""
        from .savesync import DavStorage, SaveSync
        from .sync import unprotect
        from .winapi import list_windows
        w = self.cfg["webdav"]
        storage = DavStorage(w["url"], w["user"], unprotect(w["pass"]), agent=f"PlayClock/{__version__}")
        busy = lambda: any(x.exe.startswith("ryujinx") for x in list_windows())  # noqa: E731  Ryujinx open at all
        return SaveSync(self.save_root(), storage, SAVE_STATE, SAVE_BACKUPS, self.db.device,
                        os.environ.get("COMPUTERNAME", "PC"), busy=busy)

    def save_state(self) -> dict:
        try:
            return json.loads(SAVE_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def sync_saves(self, titles: list[str] | None = None, manual: bool = False):
        """Upload changed saves / restore newer cloud saves (titles=None: every game) on the worker thread."""
        if not self.save_sync_on() or self.save_syncing:
            return
        self.save_syncing = True
        before = {t: "conflict" if g.get("remote_conflicts") else g.get("status")
                  for t, g in self.save_state().get("games", {}).items()}

        def work():
            return self.make_save_sync().sync(titles=titles)

        if titles is None:
            self.cfg["save_last"] = time.time()
        run_async(work, lambda result, error: self.saves_done(result, error, before, manual))
        if self.window:
            self.window.refresh_saves()

    def saves_done(self, result, error, before: dict, manual: bool):
        self.save_syncing = False
        if error is not None:
            msg = str(error)
            msg = tr("坚果云账号或应用密码不正确。") if msg == "auth" else (
                tr("网络连接失败，稍后会自动重试。") if msg.startswith("network") else msg)
            self.set_cfg("save_error", msg)
            if manual:
                self.tray.showMessage(APP_TITLE, tr("存档同步失败：{e}", e=msg), QSystemTrayIcon.Warning, 4000)
        else:
            self.cfg["save_error"] = ""
            save_config(self.cfg)
            names = self.save_game_names()
            games = self.save_state().get("games", {})
            restored = [names.get(t, t) for t, s in result.items() if s == "restore"]
            conflicts = [names.get(t, t) for t, s in result.items()  # here, or left by another device
                         if (s == "conflict" or games.get(t, {}).get("remote_conflicts")) and before.get(t) != "conflict"]
            waiting = [names.get(t, t) for t, s in result.items() if s == "pending_restore"]
            if restored:
                self.tray.showMessage(APP_TITLE, tr("已从云端恢复存档：{games}", games=tr("、").join(restored)),
                                      QSystemTrayIcon.Information, 5000)
            if conflicts:
                self.tray.showMessage(APP_TITLE, tr("这些游戏在两台设备上都有新进度，请在 设置 → 存档同步 里选择保留哪一份："
                                                    "{games}", games=tr("、").join(conflicts)), QSystemTrayIcon.Warning, 8000)
            elif waiting and manual:
                self.tray.showMessage(APP_TITLE, tr("云端有更新的存档，关闭 Ryujinx 后会自动恢复：{games}",
                                                    games=tr("、").join(waiting)), QSystemTrayIcon.Information, 5000)
            elif manual and not restored:
                self.tray.showMessage(APP_TITLE, tr("存档同步完成。"), QSystemTrayIcon.Information, 3000)
        if self.window:
            self.window.refresh_saves()

    def save_game_names(self) -> dict[str, str]:
        from .savesync import game_names
        root = self.save_root()
        names = game_names(root) if root else {}
        for t, g in self.save_state().get("games", {}).items():
            names.setdefault(t, (g.get("head") or {}).get("game") or t)
        for key, name in self.db.con.execute("SELECT key, game FROM game_keys WHERE key LIKE 'switch:%'"):
            names[key[7:].upper()] = self.db.resolve(name)  # PlayClock's own (unified) name for the game
        return names

    def watch_ryujinx(self):
        """Per poll: notice a Ryujinx game closing (upload its save), Ryujinx closing (bring newer saves down)
        and a game opening that another device still has open (warn)."""
        running = any(emu == "Ryujinx" for emu, _ in self.tracker.seen)
        game = None
        if running:
            for reader in self.tracker.logs.values():
                if reader.game and reader.key and reader.key.startswith("switch:"):
                    game = reader.key[7:].upper()
        if self.ryu_game and game != self.ryu_game:
            done = self.ryu_game
            QTimer.singleShot(10_000, lambda: self.sync_saves([done]))  # let the game finish writing
        if game and game != self.ryu_game:
            self.warn_open_elsewhere(game)
        if self.ryu_running and not running:
            QTimer.singleShot(5_000, self.sync_saves)
        if running and not self.ryu_running:
            waiting = [t for t, g in self.save_state().get("games", {}).items() if g.get("status") == "pending_restore"]
            if waiting and self.save_sync_on():
                names = self.save_game_names()
                self.tray.showMessage(APP_TITLE, tr("云端有更新的存档还没恢复：{games}。请先关闭 Ryujinx，恢复后再开始游戏。",
                                                    games=tr("、").join(names.get(t, t) for t in waiting)),
                                      QSystemTrayIcon.Warning, 8000)
        self.ryu_running, self.ryu_game = running, game

    def warn_open_elsewhere(self, title: str):
        if not self.save_sync_on():
            return
        for device, info in self.remote_open.items():
            if (info or {}).get("id", "").upper() == title:
                name = self.save_game_names().get(title, title)
                self.tray.showMessage(APP_TITLE, tr("{device} 上的《{game}》还开着（可能在休眠）。先在 {device} 上退出游戏，"
                                                    "否则两边进度会冲突。", device=device, game=name),
                                      QSystemTrayIcon.Warning, 8000)

    def set_save_sync(self, on: bool):
        self.set_cfg("save_sync", on)
        if on:
            self.sync_saves(manual=True)
        if self.window:
            self.window.refresh_saves()

    # --- one name per game ------------------------------------------------------
    def unify_games(self) -> int:
        """One name per game (see unify.py), then re-import Steam so merged names aren't counted twice."""
        from .emulog import ryujinx_log_dirs
        from .importers import ryujinx_roots
        from .unify import unify
        try:
            dirs = []
            for exe in [*self.cfg.get("ryujinx_paths", []), ""]:
                dirs += ryujinx_log_dirs(exe)
            steam = None
            root, acc = self.steam_account()
            if acc:
                from .steam import name_variants, scan_steam
                games = scan_steam(root, acc, i18n.LANG, self.db.steam_names())[0]
                steam = ({int(g.key.split(":")[1]): g.title for g in games}, name_variants(root))
            merged = unify(self.db, list(dict.fromkeys(dirs)), ryujinx_roots(), steam)
            if acc:
                self.sync_steam(force=True)
        except Exception as e:  # never let tidying up break the app
            self.tray.setToolTip(f"{APP_TITLE}\n" + tr("检测出错：{e}", e=e))
            return 0
        if merged:
            self.changed()
            self.tray.showMessage(APP_TITLE, tr("已把 {n} 个重复的游戏名（不同语言）合并到一起。", n=merged),
                                  QSystemTrayIcon.Information, 4000)
        return merged

    # --- floating panel -------------------------------------------------------
    def cover(self, game: str):
        from .ui.widgets import cover_pixmap
        name = self.db.get_cover(game)
        return cover_pixmap(COVERS / name) if name else None

    def set_overlay(self, on: bool, save: bool = True):
        if on and self.overlay is None:
            from .ui.overlay import Overlay
            self.overlay = Overlay(self)
            self.overlay.destroyed.connect(lambda: setattr(self, "overlay", None))
            self.overlay.show()
        elif not on and self.overlay is not None:
            self.overlay.close()  # WA_DeleteOnClose: gone, nothing left running
            self.overlay = None
            QTimer.singleShot(500, self.release_memory)
        self.overlay_action.setChecked(on)
        if save:
            self.set_cfg("overlay", on)
        if self.window:
            self.window.refresh_overlay_state()

    def toggle_overlay(self):
        self.set_overlay(self.overlay is None)

    # --- PC games added by hand (pc.py); used by the main window and the floating panel ---
    def add_pc_game(self, parent, path: str = "") -> bool:
        from .pc import is_steam_game, lookup, norm, suggest_name
        if not path:
            path, _ = QFileDialog.getOpenFileName(parent, tr("选择游戏的 exe"), "", tr("程序 (*.exe)"))
            if not path:
                return False
        path = os.path.normpath(path)
        if is_steam_game(path):
            QMessageBox.information(parent, APP_TITLE, tr("这是 Steam 游戏：它的时长由「从 Steam 导入」获取，"
                                                          "在这里再添加会重复计算。"))
            return False
        games = self.cfg.get("pc_games", [])
        if norm(path) in lookup(games):
            QMessageBox.information(parent, APP_TITLE,
                                    tr("这个程序已经添加过了：「{game}」。", game=lookup(games)[norm(path)]))
            return False
        dlg = QInputDialog(parent)
        dlg.setWindowTitle(tr("添加 PC 游戏"))
        dlg.setLabelText(tr("游戏名称：\n{path}\n可以选已有的游戏，时长会记在一起。", path=path))
        dlg.setComboBoxItems(self.db.games())
        dlg.setComboBoxEditable(True)
        dlg.setTextValue(suggest_name(path))
        dlg.resize(520, 160)
        if not dlg.exec() or not dlg.textValue().strip():
            return False
        self.set_cfg("pc_games", games + [{"path": path, "name": dlg.textValue().strip()}])
        QTimer.singleShot(0, self.on_tick)  # start counting right away, not at the next poll
        return True

    def add_running_pc_game(self, parent) -> bool:
        """Pick one of the programs that have a window open right now (start the game first)."""
        from .pc import is_steam_game, lookup, norm
        from .winapi import list_windows
        added = lookup(self.cfg.get("pc_games"))
        emulators = {w.pid for _, w in self.tracker.seen}
        choices: dict[str, str] = {}
        for w in sorted(list_windows(), key=lambda w: w.area, reverse=True):
            if (w.path and w.pid not in emulators and norm(w.path) not in added and not is_steam_game(w.path)
                    and w.path not in choices.values()):
                choices[f"{w.title}  —  {w.exe}"] = w.path
        if not choices:
            QMessageBox.information(parent, APP_TITLE, tr("没有找到可以添加的程序。请先启动游戏，再回来点这个按钮。"))
            return False
        item, ok = QInputDialog.getItem(parent, tr("添加 PC 游戏"), tr("选择正在运行的游戏（先启动游戏再来选）："),
                                        list(choices), 0, False)
        return bool(ok) and self.add_pc_game(parent, choices[item])

    def show_window(self, page: str = "home", geometry=None):
        if self.window is None:
            self.window = MainWindow(self)
            if geometry is not None:
                self.window.setGeometry(geometry)
            self.window.go(page)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self.sync_now()  # fresh numbers from other devices when the user looks

    def window_closed(self):
        """The window is gone: drop it and everything it cached."""
        self.window = None
        from .ui import widgets
        widgets.clear_caches()
        QTimer.singleShot(500, self.release_memory)
        if not self.cfg.get("tray_hint_shown"):
            self.tray.showMessage(APP_TITLE, tr("已最小化到系统托盘，会继续在后台记录。右键托盘图标可以退出。"),
                                  QSystemTrayIcon.Information, 4000)
            self.set_cfg("tray_hint_shown", True)

    def release_memory(self):
        if self.window is None and self.overlay is None:
            gc.collect()
            trim_memory()

    def set_language(self, setting: str):
        """Reopen the window in the new language; tracking keeps running."""
        self.set_cfg("language", setting)
        lang = i18n.resolve(setting)
        if lang == i18n.LANG:
            return
        apply_language(QApplication.instance(), lang)
        self.build_tray_menu()
        if self.window:
            geo = self.window.geometry()
            self.window.close()
            QTimer.singleShot(0, lambda: self.show_window("settings", geo))

    def quit(self):
        from .winapi import unregister_hotkey
        unregister_hotkey(HOTKEY_ID)
        self.timer.stop()
        self.tracker.stop()
        self.tray.hide()
        QApplication.quit()


# --------------------------------------------------------------------------- window


class MainWindow(QMainWindow):
    NAV = [("home", "概览"), ("library", "游戏库"), ("history", "历史记录"), ("stats", "统计"), ("settings", "设置")]

    def __init__(self, ctrl: Controller):
        super().__init__()
        from .ui.pages import GamePage, HistoryPage, HomePage, LibraryPage, SettingsPage, StatsPage
        self.ctrl = ctrl
        self.db, self.tracker, self.cfg = ctrl.db, ctrl.tracker, ctrl.cfg
        self.app_title, self.version, self.data_dir = APP_TITLE, __version__, DATA
        self.prev_page = "home"
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{APP_TITLE} · {tr('游戏时长记录')}")
        self.setWindowIcon(QIcon(app_icon()))
        self.resize(1240, 820)
        self.setMinimumSize(1080, 680)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(self), "library": LibraryPage(self), "history": HistoryPage(self),
            "stats": StatsPage(self), "settings": SettingsPage(self), "game": GamePage(self),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.clock = QTimer(self)  # 1 s session clock; dies with the window
        self.clock.timeout.connect(self.on_clock)
        self.clock.start(1000)
        self.update_side()

    # --- layout ------------------------------------------------------------
    def _build_sidebar(self):
        from .ui.widgets import icon, label
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 22, 16, 18)
        v.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = label()
        logo.setPixmap(app_icon(size=80).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_TITLE, "brand"))
        names.addWidget(label(tr("游戏时长记录"), "brandSub"))
        brand.addLayout(names)
        brand.addStretch()
        v.addLayout(brand)
        v.addSpacing(26)
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        for key, text in self.NAV:
            b = QPushButton("  " + tr(text))
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(icon(key))
            b.setIconSize(QSize(20, 20))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            v.addWidget(b)
        v.addStretch()
        status = QFrame()
        status.setObjectName("card")
        sv = QVBoxLayout(status)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(2)
        self.side_state = label("", "faint")
        self.side_game = label("", None)
        self.side_game.setStyleSheet("font-weight: 600;")
        self.side_time = label("", "muted")
        for w in (self.side_state, self.side_game, self.side_time):
            sv.addWidget(w)
        status.setCursor(Qt.PointingHandCursor)
        status.mouseReleaseEvent = lambda e: self.current_game() and self.open_game(self.current_game())
        v.addWidget(status)
        return side

    def go(self, key: str):
        if key != "game":
            self.prev_page = key
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if key in self.nav_buttons:
            self.nav_buttons[key].setChecked(True)
        page.refresh()

    def back(self):
        self.go(self.prev_page)

    def current_page(self):
        return self.stack.currentWidget()

    def refresh_current(self):
        self.current_page().refresh()

    # --- context API used by pages -------------------------------------------
    # Background actions live on the controller; pages reach them through the window.
    def set_cfg(self, key, value):
        self.ctrl.set_cfg(key, value)

    def set_interval(self, v):
        self.ctrl.set_interval(v)

    def set_language(self, setting):
        self.ctrl.set_language(setting)

    def sgdb_key(self):
        return self.ctrl.sgdb_key()

    def fill_missing_covers(self):
        self.ctrl.fill_missing_covers(self)

    def current_game(self):
        return self.tracker.current.game if self.tracker.current else None

    def cover(self, game: str):
        from .ui.widgets import cover_pixmap
        name = self.db.get_cover(game)
        return cover_pixmap(COVERS / name) if name else None

    def open_game(self, game: str):
        page = self.pages["game"]
        page.show_game(game)
        self.stack.setCurrentWidget(page)
        checked = self.nav.checkedButton()
        if checked:
            self.nav.setExclusive(False)
            checked.setChecked(False)
            self.nav.setExclusive(True)

    def game_menu(self, game: str, pos: QPoint):
        m = QMenu(self)
        m.addAction(tr("查看详情"), lambda: self.open_game(game))
        m.addAction(tr("在线搜索封面…"), lambda: self.search_cover(game))
        m.addAction(tr("设置封面…"), lambda: self.choose_cover(game))
        m.addAction(tr("重命名 / 合并…"), lambda: self.rename_game(game))
        m.addSeparator()
        m.addAction(tr("删除全部记录"), lambda: self.delete_game(game))
        m.exec(pos)

    def session_menu(self, table: QTableWidget, pos):
        row = table.rowAt(pos.y())
        if row < 0:
            return
        sid = table.item(row, 0).data(Qt.UserRole)
        m = QMenu(self)
        m.addAction(tr("删除这条记录"), lambda: self.delete_session(sid))
        m.exec(table.viewport().mapToGlobal(pos))

    def choose_cover(self, game: str):
        path, _ = QFileDialog.getOpenFileName(self, tr("为「{game}」选择封面", game=game), "",
                                              tr("图片") + " (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path and not self.ctrl.save_cover(game, QImage(path)):
            QMessageBox.warning(self, APP_TITLE, tr("无法读取这张图片。"))

    def clear_cover(self, game: str):
        self.ctrl.remove_cover_file(game)
        self.db.set_cover(game, None)
        self.refresh_current()

    def connect_sgdb(self) -> bool:
        """Show the key guide; True once a verified key is saved."""
        from .ui.dialogs import KeyGuideDialog
        dlg = KeyGuideDialog(self, self.ctrl.sgdb_key())
        if dlg.exec() and dlg.key:
            self.set_cfg("sgdb_key", dlg.key)
            self.refresh_current()
            return True
        return False

    def disconnect_sgdb(self):
        self.set_cfg("sgdb_key", "")
        self.refresh_current()

    def search_cover(self, game: str):
        from .ui.dialogs import CoverSearchDialog
        if not self.ctrl.sgdb_key() and not self.connect_sgdb():
            return
        dlg = CoverSearchDialog(self, self.ctrl.sgdb, game)
        if dlg.exec() and dlg.image and not self.ctrl.save_cover(game, QImage.fromData(dlg.image)):
            QMessageBox.warning(self, APP_TITLE, tr("无法读取这张图片。"))

    def open_import(self, kind: str = "ryujinx"):
        from .ui.dialogs import ImportDialog
        if kind == "steam":
            from .steam import accounts, scan_steam, steam_root
            root = steam_root()
            accs = accounts(root) if root else []
            if not accs:
                QMessageBox.information(self, APP_TITLE, tr("这台电脑上没有找到 Steam 的游玩记录。"))
                return
            current = self.cfg.get("steam_sync")
            accs.sort(key=lambda a: a.account_id != current)
            dlg = ImportDialog(
                self, self.db, "Steam", [(f"{a.persona}  ({a.account_id})", a) for a in accs],
                load=lambda a: scan_steam(root, a, i18n.LANG, self.db.steam_names()),
                source=lambda a: f"steam:{a.account_id}", resolve=self.ctrl.resolve_steam_names,
                sync_label=tr("以后自动同步这个账号的 Steam 时长"), sync_on=True)
        else:
            from .importers import RYUJINX, ryujinx_roots, scan_ryujinx
            dlg = ImportDialog(self, self.db, "Ryujinx", [(str(r), r) for r in ryujinx_roots()],
                               load=lambda root: (scan_ryujinx(root), []), source=lambda _r: RYUJINX,
                               pick_folder=True)
        if not dlg.exec():
            return
        if kind == "steam":
            acc = dlg.value
            synced = dlg.sync.isChecked()
            self.set_cfg("steam_sync", acc.account_id if synced else "")
            self.set_cfg("steam_mtime", acc.config.stat().st_mtime if synced else None)
            self.ctrl.import_art(dlg.games)
        QMessageBox.information(self, APP_TITLE, tr("已导入 {n} 款游戏的游玩记录。", n=dlg.imported))
        self.refresh_current()

    def import_suggestion(self) -> tuple[str, str] | None:
        """(banner text, import kind) for history PlayClock could import, unless dismissed."""
        if not self.cfg.get("ryujinx_banner_dismissed"):
            from .importers import RYUJINX, pending_seconds, plan_import, ryujinx_roots, scan_ryujinx
            roots = ryujinx_roots()
            if roots:
                plan = plan_import(self.db, scan_ryujinx(roots[0]))
                n = sum(1 for r in plan if r.add - self.db.imported_total(r.game, RYUJINX) >= 60)
                secs = pending_seconds(self.db, plan)
                if n and secs >= 300:
                    return tr("在 Ryujinx 里发现 {n} 款游戏共 {t} 的游玩记录，导入后统计会更完整。",
                              n=n, t=fmt_dur(secs)), "ryujinx"
        if not self.cfg.get("steam_banner_dismissed") and not self.cfg.get("steam_sync"):
            from .steam import accounts, steam_root
            root = steam_root()
            if root and accounts(root):
                return tr("检测到这台电脑上的 Steam，可以把 Steam 游戏的游玩时长一起导入。"), "steam"
        return None

    def platform_label(self, emulator: str) -> str:
        """"Switch · Ryujinx" for emulators, just "Steam" / "PS5" otherwise."""
        p = self.ctrl.platform_of(emulator)
        return emulator if p == emulator else f"{p} · {emulator}"

    def platform_of(self, emulator: str) -> str:
        return self.ctrl.platform_of(emulator)

    def connect_webdav(self):
        from .ui.dialogs import WebDavDialog
        w = self.cfg.get("webdav") or {}
        dlg = WebDavDialog(self, w.get("url", ""), w.get("user", ""), run_async)
        if dlg.exec() and dlg.result:
            self.ctrl.set_webdav(*dlg.result)
            self.refresh_current()

    def disconnect_webdav(self):
        if QMessageBox.question(self, APP_TITLE, tr("停止云同步吗？本机和坚果云上的记录都会保留。")) == QMessageBox.Yes:
            self.ctrl.clear_webdav()
            self.refresh_current()

    def sync_now(self):
        self.ctrl.sync_now(manual=True)

    def refresh_sync_state(self):
        page = self.pages["settings"]
        if self.current_page() is page:
            page.refresh_sync()

    # --- Ryujinx save sync -------------------------------------------------------
    def refresh_saves(self):
        page = self.pages["settings"]
        if self.current_page() is page:
            page.refresh_saves()

    def set_save_sync(self, on: bool):
        self.ctrl.set_save_sync(on)

    def sync_saves_now(self):
        self.ctrl.sync_saves(manual=True)

    def device_label(self, device: str) -> str:
        """A device id (or its first 8 characters, as in version file names) -> its name."""
        if self.db.device.startswith(device):
            return tr("这台电脑")
        names = self.cfg.get("sync_devices", {})
        return next((n for d, n in names.items() if d.startswith(device)), device)

    @staticmethod
    def stamp_label(stamp: str) -> str:
        """20261017T093000123Z (UTC) -> local "10-17 19:30"."""
        import calendar
        try:
            t = calendar.timegm(time.strptime(stamp[:15], "%Y%m%dT%H%M%S"))
        except ValueError:
            return stamp
        return time.strftime("%m-%d %H:%M", time.localtime(t))

    def save_job(self, work, done, busy_text: str = ""):
        """Run save-sync network/file work off the GUI thread; report errors in a dialog."""
        from .savesync import Busy

        def finished(result, error):
            if isinstance(error, Busy):
                QMessageBox.information(self, APP_TITLE, busy_text or tr("请先关闭 Ryujinx 再操作存档。"))
            elif error is not None:
                QMessageBox.warning(self, APP_TITLE, tr("存档操作失败：{e}", e=error))
            else:
                done(result)
            self.refresh_saves()

        run_async(work, finished)

    def resolve_save_conflict(self, title: str):
        """Two devices both have new progress: the user keeps one; the others' devices then take it."""
        name = self.ctrl.save_game_names().get(title, title)

        def load():
            s = self.ctrl.make_save_sync()
            return s.head(title)[0], s.conflicts(title)

        def choose(result):
            head, conflicts = result
            if not head or not conflicts:
                QMessageBox.information(self, APP_TITLE, tr("《{game}》现在没有需要处理的冲突。", game=name))
                self.ctrl.sync_saves([title])
                return
            options = {tr("云端当前的版本：{device}，{time}", device=self.device_label(head.get("device", "")) or
                          head.get("device_name", ""), time=time.strftime("%m-%d %H:%M", time.localtime(head["time"]))):
                       "head"}
            for c in conflicts:
                options[tr("{device} 的版本：{time}", device=self.device_label(c["device"]),
                           time=self.stamp_label(c["time"]))] = c["file"]
            item, ok = QInputDialog.getItem(
                self, tr("选择保留哪一份存档"),
                tr("《{game}》在两台设备上都有新进度，只能保留一份。\n没选中的那份不会删除：本机的会留在备份里，"
                   "也可以在「历史版本」里找回。", game=name), list(options), 0, False)
            if ok:
                self.save_job(lambda: self.ctrl.make_save_sync().resolve(title, options[item]),
                              lambda _r: self.ctrl.sync_saves([title], manual=True))

        self.save_job(load, choose)

    def save_history(self, title: str):
        """Put an older version of one game's save back (cloud versions and local backups)."""
        name = self.ctrl.save_game_names().get(title, title)

        def load():
            s = self.ctrl.make_save_sync()
            return s.versions(title), s.backups(title)

        def choose(result):
            versions, backups = result
            options = {}
            for v in versions:  # v-<stamp>-<device>-<hash8>.zip
                _, stamp, device, _h = v[:-4].split("-", 3)
                options[tr("云端 · {device} · {time}", device=self.device_label(device),
                           time=self.stamp_label(stamp))] = v
            for b in backups:   # <stamp>-<hash8>.zip
                options[tr("本机备份 · {time}", time=self.stamp_label(b.stem.split("-")[0]))] = b
            if not options:
                QMessageBox.information(self, APP_TITLE, tr("《{game}》还没有历史版本。", game=name))
                return
            item, ok = QInputDialog.getItem(self, tr("历史版本"), tr("用哪个版本替换《{game}》现在的存档？\n"
                                                                     "现在的存档会先备份。", game=name),
                                            list(options), 0, False)
            if not ok:
                return
            self.save_job(lambda: self.ctrl.make_save_sync().restore_version(title, options[item]),
                          lambda _r: (self.ctrl.sync_saves([title], manual=True),
                                      self.ctrl.tray.showMessage(APP_TITLE, tr("已恢复《{game}》的存档。", game=name),
                                                                 QSystemTrayIcon.Information, 3000)))

        self.save_job(load, choose)

    def add_time(self, game: str = "", platform: str = ""):
        from .ui.dialogs import AddTimeDialog
        known = sorted({self.platform_of(e) for e in self.db.emulators()} | {"Switch", "PS5", "PS4", "Xbox", "PC"})
        dlg = AddTimeDialog(self, self.db.games(), known, game, platform)
        if dlg.exec():
            self.db.add_manual(dlg.game, dlg.platform, dlg.seconds, dlg.end)
            self.refresh_current()

    # --- PC games added by hand (pc.py) --------------------------------------
    def set_overlay(self, on: bool):
        self.ctrl.set_overlay(on)

    def refresh_overlay_state(self):
        page = self.pages["settings"]
        if self.current_page() is page:
            page.refresh_overlay()

    def add_pc_game(self, path: str = ""):
        if self.ctrl.add_pc_game(self, path):
            self.refresh_current()

    def add_running_pc_game(self):
        if self.ctrl.add_running_pc_game(self):
            self.refresh_current()

    def remove_pc_game(self, path: str):
        from .pc import norm
        games = self.cfg.get("pc_games", [])
        g = next((g for g in games if norm(g["path"]) == norm(path)), None)
        if g and QMessageBox.question(self, APP_TITLE, tr("不再为「{game}」自动计时吗？已有的游玩记录会保留。",
                                                          game=g["name"])) == QMessageBox.Yes:
            self.ctrl.set_cfg("pc_games", [x for x in games if x is not g])
            self.refresh_current()

    def rename_game(self, game: str):
        dlg = QInputDialog(self)
        dlg.setWindowTitle(tr("重命名 / 合并"))
        dlg.setLabelText(tr("「{game}」的新名称：\n输入已有游戏的名称即可把两者合并，以后识别到的同名游戏也会自动归入。", game=game))
        dlg.setComboBoxItems(self.db.games())
        dlg.setComboBoxEditable(True)
        dlg.setTextValue(game)
        dlg.resize(460, 160)
        if not dlg.exec():
            return
        new = dlg.textValue().strip()
        if new and new != game:
            if self.db.get_cover(new) and self.db.get_cover(game):
                self.ctrl.remove_cover_file(game)
            self.tracker.flush()
            self.db.rename_game(game, new)
            if self.current_page() is self.pages["game"]:
                self.pages["game"].game = new
            self.refresh_current()

    def delete_game(self, game: str):
        if QMessageBox.question(self, APP_TITLE, tr("确定删除「{game}」的全部游玩记录吗？\n此操作无法撤销。", game=game)) != QMessageBox.Yes:
            return
        if self.current_game() == game:
            self.tracker.current = None
        self.ctrl.remove_cover_file(game)
        self.db.delete_game(game)
        if self.current_page() is self.pages["game"]:
            self.back()
        else:
            self.refresh_current()

    def delete_session(self, sid: int):
        if QMessageBox.question(self, APP_TITLE, tr("确定删除这条记录吗？")) != QMessageBox.Yes:
            return
        if self.tracker.current and self.tracker.current.sid == sid:
            self.tracker.current = None
        self.db.delete_session(sid)
        self.refresh_current()

    def get_autostart(self):
        return get_autostart()

    def set_autostart(self, on: bool):
        try:
            set_autostart(on)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, tr("设置开机启动失败：{e}", e=e))

    def edit_rules(self):
        ensure_user_file(USER_RULES_PATH)
        os.startfile(USER_RULES_PATH)

    def reload_rules(self):
        try:
            self.tracker.rules = load_rules(USER_RULES_PATH)
        except Exception as e:  # bad JSON / regex typed by the user
            QMessageBox.warning(self, APP_TITLE, tr("规则文件有错误，未加载：\n{e}", e=e))
            return
        QMessageBox.information(self, APP_TITLE, tr("已加载 {n} 条规则。", n=len(self.tracker.rules)))
        self.refresh_current()

    def open_data(self):
        os.startfile(DATA)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("导出 CSV"), str(Path.home() / "Desktop" / "playtime.csv"),
                                              "CSV (*.csv)")
        if path:
            n = self.db.export_csv(Path(path))
            QMessageBox.information(self, APP_TITLE, tr("已导出 {n} 条记录。", n=n))

    # --- ticking -----------------------------------------------------------
    def on_tick(self, game_changed: bool):
        if game_changed:
            self.refresh_current()
        else:
            self.current_page().on_tick()
        self.update_side()

    def on_clock(self):
        """Advance the visible session clock between polls."""
        cur = self.tracker.current
        page = self.pages["home"]
        if cur and self.current_page() is page and self.tracker.last_tick:
            page.hero.seconds = cur.seconds + min(time.time() - self.tracker.last_tick, self.cfg["interval"])
            page.hero.update()

    def update_side(self):
        cur = self.tracker.current
        if cur:
            self.side_state.setText(tr("●  正在记录"))
            self.side_state.setStyleSheet(f"color: {theme.GREEN}; font-size: 9pt;")
            self.side_game.setText(self.side_game.fontMetrics().elidedText(cur.game, Qt.ElideRight, 170))
            self.side_time.setText(tr("本次 {t}", t=fmt_dur(cur.seconds)))
        else:
            self.side_state.setText(tr("○  等待游戏启动"))
            self.side_state.setStyleSheet(f"color: {theme.FAINT}; font-size: 9pt;")
        self.side_game.setVisible(bool(cur))
        self.side_time.setVisible(bool(cur))

    def closeEvent(self, e):
        self.clock.stop()
        super().closeEvent(e)
        self.ctrl.window_closed()


# --------------------------------------------------------------------------- main


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)

    # Single instance: a second launch just asks the first one to show its window.
    sock = QLocalSocket()
    sock.connectToServer(APP_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(APP_NAME)
    server = QLocalServer()
    server.listen(APP_NAME)

    theme.apply(app)
    cfg = load_config()
    save_config(cfg)
    apply_language(app, i18n.resolve(cfg["language"]))
    if not cfg.get("device_id"):  # identifies this PC's records in cloud sync
        cfg["device_id"] = uuid.uuid4().hex
        save_config(cfg)
    db = DB(DATA / "playtime.db", device=cfg["device_id"])
    db.drop_short(float(cfg["min_session"]))  # leftovers from a crash / power cut
    if LEGACY_RULES_PATH.exists():
        LEGACY_RULES_PATH.replace(LEGACY_RULES_PATH.with_suffix(".json.bak"))
    ensure_user_file(USER_RULES_PATH)
    write_builtin_reference(BUILTIN_RULES_PATH)
    try:
        rules = load_rules(USER_RULES_PATH)
    except Exception as e:  # bad JSON / regex typed by the user: fall back to built-ins
        QMessageBox.warning(None, APP_TITLE, tr("自定义规则文件有错误，本次只使用内置规则：\n{path}\n\n{e}",
                                                path=USER_RULES_PATH, e=e))
        rules = load_rules()

    ctrl = Controller(db, Tracker(db, rules, cfg), cfg)
    # Shutdown / logoff usually ends a tray app without a normal quit: write the running session first.
    app.commitDataRequest.connect(lambda _m: ctrl.tracker.flush())
    app.aboutToQuit.connect(ctrl.tracker.stop)
    from .winapi import register_hotkey
    hotkeys = HotkeyFilter(ctrl.toggle_overlay)
    app.installNativeEventFilter(hotkeys)
    app._hotkeys = hotkeys  # keep the filter alive
    ctrl.hotkey_ok = register_hotkey(HOTKEY_ID)
    if cfg.get("overlay"):
        ctrl.set_overlay(True, save=False)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctrl.show_window()))
    if "--minimized" not in sys.argv:
        ctrl.show_window()
    else:
        QTimer.singleShot(8000, ctrl.release_memory)  # after the startup sync (5 s)
    return app.exec()
