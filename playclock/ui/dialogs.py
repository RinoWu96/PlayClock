"""Dialogs: SteamGridDB key guide, online cover search, emulator history import."""
from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..covers import KEY_URL, SITE_URL, SteamGridDB
from ..i18n import fmt_dur, tr
from . import theme
from .widgets import FlowLayout, label


def _button(text, kind=None, slot=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


class Dialog(QDialog):
    def __init__(self, parent, title: str, width: int = 640):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG}; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(28, 24, 28, 22)
        self.lay.setSpacing(14)

    def footer(self, *buttons):
        row = QHBoxLayout()
        self.status = label("", "muted", wrap=True)
        row.addWidget(self.status, 1)
        for b in buttons:
            row.addWidget(b)
        self.lay.addLayout(row)

    def set_status(self, text: str, error: bool = False):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.DANGER if error else theme.MUTED};")


# --------------------------------------------------------------------------- key guide


class Step(QFrame):
    def __init__(self, n: int, title: str, body: str, action: QWidget | None = None):
        super().__init__()
        self.setObjectName("card")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(14)
        num = label(str(n))
        num.setFixedSize(30, 30)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"background: {theme.ACCENT}; color: white; border-radius: 15px; font-weight: 700;")
        h.addWidget(num, 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(3)
        t = label(title)
        t.setStyleSheet("font-weight: 600;")
        v.addWidget(t)
        v.addWidget(label(body, "muted", wrap=True))
        self.body = v
        h.addLayout(v, 1)
        if action:
            h.addWidget(action, 0, Qt.AlignVCenter)


class KeyGuideDialog(Dialog):
    """Walks the user through getting a SteamGridDB API key, then verifies and returns it."""

    def __init__(self, parent, current_key: str = ""):
        super().__init__(parent, tr("连接 SteamGridDB"), 660)
        self.key = ""
        self.lay.addWidget(label(tr("连接 SteamGridDB"), "pageTitle"))
        self.lay.addWidget(label(tr("SteamGridDB 是免费的社区游戏封面库，主机游戏也很全。连接后可以在线搜索封面，"
                                    "还能为新游戏自动配上封面。只需 3 步，大约 1 分钟。"), "muted", wrap=True))
        open_site = _button(tr("打开网站"), slot=lambda: QDesktopServices.openUrl(QUrl(SITE_URL)))
        self.lay.addWidget(Step(1, tr("登录 SteamGridDB"),
                                tr("在网站右上角点「Login」，可以直接用 Steam 账号登录，完全免费。"), open_site))
        open_key = _button(tr("打开 API 页面"), slot=lambda: QDesktopServices.openUrl(QUrl(KEY_URL)))
        self.lay.addWidget(Step(2, tr("生成 API Key"),
                                tr("登录后再点一次「打开 API 页面」（没登录时会跳到登录页），点击「Generate API Key」，复制生成的密钥。也可以在网站右上角头像 → Preferences → API 找到。"), open_key))
        step3 = Step(3, tr("粘贴到这里"), tr("密钥只保存在这台电脑上，不会上传到别处。"))
        row = QHBoxLayout()
        self.edit = QLineEdit(current_key)
        self.edit.setPlaceholderText(tr("粘贴 API Key"))
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setMinimumHeight(38)
        self.edit.returnPressed.connect(self.verify)
        row.addWidget(self.edit, 1)
        row.addWidget(_button(tr("粘贴"), slot=lambda: self.edit.setText(QGuiApplication.clipboard().text().strip())))
        show = _button(tr("显示"))
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        row.addWidget(show)
        step3.body.addLayout(row)
        self.lay.addWidget(step3)
        self.lay.addWidget(label(tr("搜索封面时会把游戏名发送给 SteamGridDB（中文、日文名还会发给维基百科查英文名）；不使用封面功能时 PlayClock 不会联网。"),
                                 "faint", wrap=True))
        self.ok = _button(tr("验证并保存"), "primary", self.verify)
        self.footer(_button(tr("取消"), slot=self.reject), self.ok)
        self.client = None

    def verify(self):
        key = self.edit.text().strip()
        if not key:
            self.set_status(tr("请先粘贴 API Key。"), True)
            return
        self.ok.setEnabled(False)
        self.set_status(tr("正在验证…"))
        self.client = SteamGridDB(key, self)

        def done(ok, err):
            if not isValid(self):
                return
            self.ok.setEnabled(True)
            if ok:
                self.key = key
                self.accept()
            else:
                self.set_status(err, True)

        self.client.verify(done)


# --------------------------------------------------------------------------- cover search


class Thumb(QWidget):
    clicked = Signal(dict)
    W, H = 230, 108

    def __init__(self, grid: dict):
        super().__init__()
        self.grid = grid
        self.pixmap = None
        self.hover = False
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)

    def set_pixmap(self, pm):
        self.pixmap = pm
        self.update()

    def enterEvent(self, _):
        self.hover = True
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.grid)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        p.fillRect(r, QColor(theme.CARD))
        if self.pixmap:
            s = self.pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(int((self.width() - s.width()) / 2), int((self.height() - s.height()) / 2), s)
        else:
            p.setPen(QColor(theme.FAINT))
            p.drawText(r, Qt.AlignCenter, tr("加载中…"))
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.ACCENT if self.hover else theme.BORDER), 2 if self.hover else 1))
        p.drawRoundedRect(r, 10, 10)


class CoverSearchDialog(Dialog):
    """Search SteamGridDB by name, pick a game, click a grid image -> self.image holds its bytes."""

    def __init__(self, parent, client: SteamGridDB, game: str):
        super().__init__(parent, tr("在线搜索封面"), 980)
        self.resize(1040, 680)
        self.image = None
        self.client = client
        self.lay.addWidget(label(tr("为「{game}」选择封面", game=game), "sectionTitle"))
        row = QHBoxLayout()
        self.query = QLineEdit(game)
        self.query.setMinimumHeight(38)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(_button(tr("搜索"), "primary", self.search))
        self.lay.addLayout(row)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.results = QListWidget()
        self.results.setFixedWidth(280)
        self.results.setStyleSheet(
            f"QListWidget {{ background: {theme.CARD}; border: 1px solid {theme.BORDER}; border-radius: 12px; padding: 6px; }}"
            f"QListWidget::item {{ padding: 8px; border-radius: 8px; }}"
            f"QListWidget::item:selected {{ background: {theme.CARD_HOVER}; color: {theme.TEXT}; }}")
        self.results.currentItemChanged.connect(self.pick_game)
        body.addWidget(self.results)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        grid_box = QWidget()
        self.grid = FlowLayout(grid_box, 12)
        scroll.setWidget(grid_box)
        body.addWidget(scroll, 1)
        self.lay.addLayout(body, 1)
        self.footer(_button(tr("取消"), slot=self.reject))
        self.search()

    def search(self):
        term = self.query.text().strip()
        if not term:
            return
        self.results.clear()
        self.grid.clear()
        self.set_status(tr("正在搜索…"))

        def done(games, err, used):
            if not isValid(self):
                return
            if err:
                self.set_status(err, True)
                return
            if not games:
                self.set_status(tr("没有找到匹配的游戏，换个名字试试（英文名通常更容易搜到）。"))
                return
            self.set_status((tr("已按英文名「{name}」搜索。", name=used) + " " if used != term else "")
                            + tr("选择左侧的游戏，再点击喜欢的封面。"))
            for g in games:
                it = QListWidgetItem(f"{g['name']}  ({g['year']})" if g["year"] else g["name"])
                it.setData(Qt.UserRole, g["id"])
                self.results.addItem(it)
            self.results.setCurrentRow(0)

        self.client.search_any(term, done)

    def pick_game(self, item, _prev=None):
        if not item:
            return
        self.grid.clear()
        self.set_status(tr("正在加载封面…"))
        gid = item.data(Qt.UserRole)

        def done(grids, err):
            if not isValid(self) or self.results.currentItem() is not item:
                return
            if err:
                self.set_status(err, True)
                return
            self.set_status(tr("共 {n} 张封面，点击即可使用。", n=len(grids)) if grids
                            else tr("这个游戏还没有横版封面，试试其他搜索结果。"))
            for g in grids[:60]:
                t = Thumb(g)
                t.clicked.connect(self.choose)
                self.grid.addWidget(t)
                self.client.download(g["thumb"], lambda data, e, t=t: self._thumb_loaded(t, data))

        self.client.grids(gid, done)

    @staticmethod
    def _thumb_loaded(thumb, data):
        if data and isValid(thumb):
            pm = QPixmap()
            if pm.loadFromData(data):
                thumb.set_pixmap(pm)

    def choose(self, grid):
        self.set_status(tr("正在下载封面…"))

        def done(data, err):
            if not isValid(self):
                return
            if err or not data:
                self.set_status(err or tr("下载失败。"), True)
                return
            self.image = data
            self.accept()

        self.client.download(grid["url"], done)


# --------------------------------------------------------------------------- history import


class ImportDialog(Dialog):
    """Preview and confirm importing an emulator's own playtime records."""

    def __init__(self, parent, db, roots, scan, plan_fn, apply_fn, emulator: str = "Ryujinx"):
        super().__init__(parent, tr("导入 {emu} 的游玩记录", emu=emulator), 760)
        self.resize(820, 600)
        self.db, self.scan, self.plan_fn, self.apply_fn, self.emulator = db, scan, plan_fn, apply_fn, emulator
        self.imported = 0
        self.lay.addWidget(label(tr("导入 {emu} 的游玩记录", emu=emulator), "pageTitle"))
        self.lay.addWidget(label(tr("{emu} 自己也会记录每个游戏的累计时长。导入后，安装 PlayClock 之前玩的时间也能算进统计。"
                                    "只会补上 PlayClock 没记录到的部分，重复导入也不会重复计算。", emu=emulator),
                                 "muted", wrap=True))
        src = QHBoxLayout()
        self.src_label = label("", "faint", wrap=True)
        src.addWidget(self.src_label, 1)
        src.addWidget(_button(tr("选择其他文件夹…"), slot=self.pick_folder))
        self.lay.addLayout(src)
        from .pages import make_table
        self.table = make_table([tr("游戏"), tr("{emu} 记录", emu=emulator), tr("PlayClock 已记录"), tr("将导入")])
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.lay.addWidget(self.table, 1)
        self.go = _button(tr("导入"), "primary", self.do_import)
        self.footer(_button(tr("取消"), slot=self.reject), self.go)
        self.load(roots[0] if roots else None)

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择 {emu} 数据文件夹", emu=self.emulator))
        if path:
            from pathlib import Path
            self.load(Path(path))

    def load(self, root):
        self.plan = []
        if root is None:
            self.src_label.setText(tr("没有在默认位置找到 {emu} 的数据。如果你用的是便携版，请选择它的文件夹。",
                                      emu=self.emulator))
        else:
            self.plan = self.plan_fn(self.db, self.scan(root))
            self.src_label.setText(tr("数据来源：{path}", path=root))
        self.table.setRowCount(len(self.plan))
        right = Qt.AlignRight | Qt.AlignVCenter
        for i, r in enumerate(self.plan):
            cells = [r.game, fmt_dur(r.emulator_total), fmt_dur(r.tracked), fmt_dur(r.add) if r.add >= 60 else "—"]
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if j:
                    it.setTextAlignment(right)
                if j == 3 and r.add >= 60:
                    it.setForeground(QColor(theme.GREEN))
                self.table.setItem(i, j, it)
        total = sum(r.add for r in self.plan)
        self.go.setEnabled(total >= 60)
        if root is not None:
            self.set_status(tr("共 {n} 款游戏，将导入 {t}。", n=len(self.plan), t=fmt_dur(total)) if self.plan
                            else tr("这个文件夹里没有找到游玩记录。"))

    def do_import(self):
        self.imported = self.apply_fn(self.db, self.plan)
        self.accept()
