"""Entry point. A small always-on Controller (tracker, tray, timer) plus a main window that only exists
while it is open — closing it frees all UI memory, so the background footprint stays minimal."""
import gc
import json
import os
import sys
import time
import uuid
import winreg
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QObject, QPoint, QSize, Qt, QTimer, QTranslator
from PySide6.QtGui import QIcon, QImage
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QSystemTrayIcon, QTableWidget, QVBoxLayout, QWidget,
)

from . import __version__, i18n
from .db import DB
from .i18n import fmt_dur, tr
from .rules import ensure_user_file, load_rules, write_builtin_reference
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


class Controller(QObject):
    """Everything that runs while PlayClock sits in the tray."""

    def __init__(self, db: DB, tracker: Tracker, cfg: dict):
        super().__init__()
        self.db, self.tracker, self.cfg = db, tracker, cfg
        self.window = None
        self.last_game = None
        self._sgdb = None
        self.cover_tried: set[str] = set()

        self.tray = QSystemTrayIcon(QIcon(app_icon()), self)
        self.tray.activated.connect(
            lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.build_tray_menu()
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(int(self.cfg["interval"] * 1000))
        self.on_tick()

    def build_tray_menu(self):
        self.menu = QMenu()
        self.menu.addAction(tr("打开主界面"), self.show_window)
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

    def save_cover(self, game: str, img: QImage) -> bool:
        if img.isNull():
            return False
        if img.width() > 1280:
            img = img.scaledToWidth(1280, Qt.SmoothTransformation)
        COVERS.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.png"
        img.save(str(COVERS / name))
        self.remove_cover_file(game)
        self.db.set_cover(game, name)
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
    def show_window(self, page: str = "home", geometry=None):
        if self.window is None:
            self.window = MainWindow(self)
            if geometry is not None:
                self.window.setGeometry(geometry)
            self.window.go(page)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

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
        if self.window is None:
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
        self.setWindowTitle(f"{APP_TITLE} · {tr('模拟器游戏时长记录')}")
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
        names.addWidget(label(tr("模拟器游戏时长记录"), "brandSub"))
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

    def open_import(self):
        from .importers import apply_import, plan_import, ryujinx_roots, scan_ryujinx
        from .ui.dialogs import ImportDialog
        dlg = ImportDialog(self, self.db, ryujinx_roots(), scan_ryujinx, plan_import, apply_import)
        if dlg.exec():
            QMessageBox.information(self, APP_TITLE, tr("已导入 {n} 款游戏的游玩记录。", n=dlg.imported))
            self.refresh_current()

    def ryujinx_pending(self) -> tuple[int, float]:
        """(games, seconds) Ryujinx has recorded that PlayClock doesn't have yet."""
        from .importers import RYUJINX, pending_seconds, plan_import, ryujinx_roots, scan_ryujinx
        roots = ryujinx_roots()
        if not roots:
            return 0, 0.0
        plan = plan_import(self.db, scan_ryujinx(roots[0]))
        rows = [r for r in plan if r.add - self.db.imported_total(r.game, RYUJINX) >= 60]
        return len(rows), pending_seconds(self.db, plan)

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
    db = DB(DATA / "playtime.db")
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
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctrl.show_window()))
    if "--minimized" not in sys.argv:
        ctrl.show_window()
    else:
        QTimer.singleShot(3000, ctrl.release_memory)
    return app.exec()
