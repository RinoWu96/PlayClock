"""Main window, tray icon and application entry point."""
import json
import os
import sys
import time
import uuid
import winreg
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QPoint, QSize, Qt, QTimer, QTranslator
from PySide6.QtGui import QIcon, QImage
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QSystemTrayIcon, QTableWidget, QVBoxLayout, QWidget,
)

from . import __version__
from . import i18n
from .db import DB
from .i18n import tr
from .rules import ensure_user_file, load_rules, write_builtin_reference
from .tracker import Tracker
from .ui import theme
from .ui.pages import GamePage, HistoryPage, HomePage, LibraryPage, SettingsPage, StatsPage
from .ui.widgets import app_icon, cover_pixmap, fmt_dur, icon, label

APP_NAME = "EmuPlaytime"
APP_TITLE = "EmuPlaytime"
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


class MainWindow(QMainWindow):
    NAV = [("home", "概览"), ("library", "游戏库"), ("history", "历史记录"), ("stats", "统计"), ("settings", "设置")]

    def __init__(self, db: DB, tracker: Tracker, cfg: dict, rebuild=None):
        super().__init__()
        self.db, self.tracker, self.cfg = db, tracker, cfg
        self.rebuild = rebuild
        self.app_title, self.version, self.data_dir = APP_TITLE, __version__, DATA
        self.quitting = False
        self.last_game = None
        self.prev_page = "home"
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

        self.tray = QSystemTrayIcon(QIcon(app_icon()), self)
        menu = QMenu()
        menu.addAction(tr("打开主界面"), self.show_window)
        menu.addSeparator()
        menu.addAction(tr("退出"), self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(int(self.cfg["interval"] * 1000))
        self.clock = QTimer(self)          # 1 s UI clock for the "now playing" timer
        self.clock.timeout.connect(self.on_clock)
        self.clock.start(1000)
        self.on_tick()
        self.go("home")

    # --- layout ------------------------------------------------------------
    def _build_sidebar(self):
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
        self.side_status = QFrame()
        self.side_status.setObjectName("card")
        sv = QVBoxLayout(self.side_status)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(2)
        self.side_state = label("", "faint")
        self.side_game = label("", None)
        self.side_game.setStyleSheet("font-weight: 600;")
        self.side_time = label("", "muted")
        sv.addWidget(self.side_state)
        sv.addWidget(self.side_game)
        sv.addWidget(self.side_time)
        self.side_status.setCursor(Qt.PointingHandCursor)
        self.side_status.mouseReleaseEvent = lambda e: self.current_game() and self.open_game(self.current_game())
        v.addWidget(self.side_status)
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
    def current_game(self):
        return self.tracker.current.game if self.tracker.current else None

    def cover(self, game: str):
        name = self.db.get_cover(game)
        return cover_pixmap(COVERS / name) if name else None

    def open_game(self, game: str):
        page = self.pages["game"]
        page.show_game(game)
        self.stack.setCurrentWidget(page)
        for b in self.nav_buttons.values():
            if b.isChecked():
                self.nav.setExclusive(False)
                b.setChecked(False)
                self.nav.setExclusive(True)

    def game_menu(self, game: str, pos: QPoint):
        m = QMenu(self)
        m.addAction(tr("查看详情"), lambda: self.open_game(game))
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
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, APP_TITLE, tr("无法读取这张图片。"))
            return
        if img.width() > 1280:
            img = img.scaledToWidth(1280, Qt.SmoothTransformation)
        COVERS.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.png"
        img.save(str(COVERS / name))
        self._remove_cover_file(game)
        self.db.set_cover(game, name)
        self.refresh_current()

    def clear_cover(self, game: str):
        self._remove_cover_file(game)
        self.db.set_cover(game, None)
        self.refresh_current()

    def _remove_cover_file(self, game: str):
        old = self.db.get_cover(game)
        if old:
            try:
                (COVERS / old).unlink()
            except OSError:
                pass

    def rename_game(self, game: str):
        games = self.db.games()
        dlg = QInputDialog(self)
        dlg.setWindowTitle(tr("重命名 / 合并"))
        dlg.setLabelText(tr("「{game}」的新名称：\n输入已有游戏的名称即可把两者合并，以后识别到的同名游戏也会自动归入。", game=game))
        dlg.setComboBoxItems(games)
        dlg.setComboBoxEditable(True)
        dlg.setTextValue(game)
        dlg.resize(460, 160)
        if not dlg.exec():
            return
        new = dlg.textValue().strip()
        if new and new != game:
            if self.db.get_cover(new) and self.db.get_cover(game):
                self._remove_cover_file(game)
            self.db.rename_game(game, new)
            if self.current_page() is self.pages["game"]:
                self.pages["game"].game = new
            self.refresh_current()

    def delete_game(self, game: str):
        if QMessageBox.question(self, APP_TITLE, tr("确定删除「{game}」的全部游玩记录吗？\n此操作无法撤销。", game=game)) != QMessageBox.Yes:
            return
        if self.current_game() == game:
            self.tracker.current = None
        self._remove_cover_file(game)
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

    def set_cfg(self, key, value):
        self.cfg[key] = value
        save_config(self.cfg)

    def set_interval(self, v):
        self.set_cfg("interval", v)
        self.timer.setInterval(v * 1000)

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
    def on_tick(self):
        try:
            cur = self.tracker.tick()
        except Exception as e:  # never let one bad poll kill the tracker
            self.side_state.setText(tr("检测出错：{e}", e=e))
            return
        game = cur.game if cur else None
        if cur:
            tip = tr("正在游玩：{game}\n本次 {t}", game=cur.game, t=fmt_dur(cur.seconds))
        else:
            tip = tr("等待游戏启动")
        self.tray.setToolTip(f"{APP_TITLE}\n{tip}")
        if game != self.last_game:
            self.tray.setIcon(QIcon(app_icon(active=bool(cur))))
            self.last_game = game
            if self.isVisible():
                self.refresh_current()
        elif self.isVisible():
            self.current_page().on_tick()
        self.update_side()

    def on_clock(self):
        """Advance the visible session clock between polls."""
        cur = self.tracker.current
        if not cur or not self.isVisible():
            return
        page = self.pages["home"]
        if self.current_page() is page and self.tracker.last_tick:
            extra = min(time.time() - self.tracker.last_tick, self.cfg["interval"])
            page.hero.seconds = cur.seconds + extra
            page.hero.update()

    def update_side(self):
        cur = self.tracker.current
        if cur:
            self.side_state.setText(tr("●  正在记录"))
            self.side_state.setStyleSheet(f"color: {theme.GREEN}; font-size: 9pt;")
            self.side_game.setText(self.side_game.fontMetrics().elidedText(cur.game, Qt.ElideRight, 170))
            self.side_game.show()
            self.side_time.setText(tr("本次 {t}", t=fmt_dur(cur.seconds)))
            self.side_time.show()
        else:
            self.side_state.setText(tr("○  等待游戏启动"))
            self.side_state.setStyleSheet(f"color: {theme.FAINT}; font-size: 9pt;")
            self.side_game.hide()
            self.side_time.hide()

    # --- window / tray -----------------------------------------------------
    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_current()

    def closeEvent(self, e):
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.hide()
        if not self.cfg.get("tray_hint_shown"):
            self.tray.showMessage(APP_TITLE, tr("已最小化到系统托盘，会继续在后台记录。右键托盘图标可以退出。"),
                                  QSystemTrayIcon.Information, 4000)
            self.set_cfg("tray_hint_shown", True)

    def set_language(self, setting: str):
        """Switch UI language right away by rebuilding the window; tracking keeps running."""
        self.set_cfg("language", setting)
        lang = i18n.resolve(setting)
        if lang != i18n.LANG and self.rebuild:
            apply_language(QApplication.instance(), lang)
            QTimer.singleShot(0, lambda: self.rebuild(self))

    def dispose(self):
        """Tear down without quitting the app (used when the window is rebuilt)."""
        self.quitting = True
        self.timer.stop()
        self.clock.stop()
        self.tray.hide()
        self.close()
        self.deleteLater()

    def quit(self):
        self.quitting = True
        self.timer.stop()
        self.tracker.stop()
        self.tray.hide()
        QApplication.quit()


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
        QMessageBox.warning(None, APP_TITLE, tr("自定义规则文件有错误，本次只使用内置规则：\n{path}\n\n{e}", path=USER_RULES_PATH, e=e))
        rules = load_rules()
    tracker = Tracker(db, rules, cfg)
    holder = {}

    def rebuild(old: MainWindow):
        new = MainWindow(db, tracker, cfg, rebuild)
        new.setGeometry(old.geometry())
        visible = old.isVisible()
        old.dispose()
        holder["win"] = new
        new.go("settings")
        if visible:
            new.show_window()

    holder["win"] = MainWindow(db, tracker, cfg, rebuild)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), holder["win"].show_window()))
    if "--minimized" not in sys.argv:
        holder["win"].show_window()
    return app.exec()
