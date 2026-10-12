"""Dialogs: SteamGridDB key guide, online cover search, history import, manual time."""
import time

from PySide6.QtCore import QDate, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QTableWidgetItem, QVBoxLayout, QWidget,
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
    """Preview and confirm importing a platform's own playtime records (Ryujinx, Steam...).

    choices      [(label, value)] — data folders or accounts to import from
    load(value)  -> (games, unknown_ids); unknown ids are entries whose name must be looked up
    source(value) -> source tag stored on imported sessions
    resolve(ids, done) -> look names up (async), then call done(); optional
    """

    def __init__(self, parent, db, emulator: str, choices, load, source, resolve=None,
                 pick_folder: bool = False, sync_label: str | None = None, sync_on: bool = False):
        super().__init__(parent, tr("导入 {emu} 的游玩记录", emu=emulator), 760)
        self.resize(860, 640)
        self.db, self.emulator, self.load_fn, self.source_fn, self.resolve = db, emulator, load, source, resolve
        self.imported = 0
        self.resolved = False   # names were looked up already
        self.resolving = False  # lookup in progress
        self.plan = []
        self.lay.addWidget(label(tr("导入 {emu} 的游玩记录", emu=emulator), "pageTitle"))
        self.lay.addWidget(label(tr("{emu} 自己也会记录每个游戏的累计时长。导入后，安装 PlayClock 之前玩的时间也能算进统计。"
                                    "只会补上 PlayClock 没记录到的部分，重复导入也不会重复计算。", emu=emulator),
                                 "muted", wrap=True))
        src = QHBoxLayout()
        self.choice = QComboBox()
        self.choice.setMinimumHeight(36)
        for text, value in choices:
            self.choice.addItem(text, value)
        self.choice.currentIndexChanged.connect(lambda _: self.load(self.choice.currentData()))
        src.addWidget(self.choice, 1)
        if pick_folder:
            src.addWidget(_button(tr("选择其他文件夹…"), slot=self.pick_folder))
        self.lay.addLayout(src)
        self.src_label = label("", "faint", wrap=True)
        self.lay.addWidget(self.src_label)
        from .pages import make_table
        self.table = make_table([tr("游戏"), tr("{emu} 记录", emu=emulator), tr("PlayClock 已记录"), tr("将导入")])
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.lay.addWidget(self.table, 1)
        self.sync = None
        if sync_label:
            from .widgets import Toggle
            self.sync = Toggle(sync_label)
            self.sync.setChecked(sync_on)
            self.lay.addWidget(self.sync)
        self.go = _button(tr("导入"), "primary", self.do_import)
        self.footer(_button(tr("取消"), slot=self.reject), self.go)
        if choices:
            self.load(choices[0][1])
        else:
            self.choice.hide()
            self.src_label.setText(tr("没有在默认位置找到 {emu} 的数据。如果你用的是便携版，请选择它的文件夹。",
                                      emu=emulator))
            self.go.setEnabled(False)

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择 {emu} 数据文件夹", emu=self.emulator))
        if path:
            from pathlib import Path
            self.choice.show()
            self.choice.addItem(path, Path(path))
            self.choice.setCurrentIndex(self.choice.count() - 1)

    def load(self, value):
        self.value = value
        games, unknown = self.load_fn(value)
        self.games = games
        if unknown and self.resolve and not self.resolved:
            self.resolved = self.resolving = True  # look names up once, then reload

            def done():
                if isValid(self):
                    self.resolving = False
                    self.load(self.value)

            self.resolve(unknown, done)
        from ..importers import plan_import
        self.plan = sorted(plan_import(self.db, games, self.emulator), key=lambda r: -r.add)
        self.src_label.setText(tr("数据来源：{path}", path=getattr(value, "config", value)))
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
        if self.resolving:
            self.go.setEnabled(False)
            self.set_status(tr("正在联网查询 {n} 个游戏的名字…", n=len(unknown)))
        else:
            self.go.setEnabled(total >= 60)
            msg = (tr("共 {n} 款游戏，将导入 {t}。", n=len(self.plan), t=fmt_dur(total)) if self.plan
                   else tr("这个文件夹里没有找到游玩记录。"))
            if unknown:
                msg += " " + tr("另有 {n} 个应用查不到名字，已跳过。", n=len(unknown))
            self.set_status(msg)

    def do_import(self):
        from ..importers import apply_import
        self.imported = apply_import(self.db, self.plan, self.emulator, self.source_fn(self.value))
        self.accept()


# --------------------------------------------------------------------------- manual time


class AddTimeDialog(Dialog):
    """Add playtime by hand — for consoles and anything PlayClock can't see."""

    def __init__(self, parent, games: list[str], platforms: list[str], game: str = "", platform: str = ""):
        super().__init__(parent, tr("添加游玩时长"), 520)
        self.lay.addWidget(label(tr("添加游玩时长"), "pageTitle"))
        self.lay.addWidget(label(tr("记录在主机或其他设备上玩的时间，比如 Switch、PS5。"
                                    "时长可以对照主机「个人资料」或 PS App 里显示的数字填写。"), "muted", wrap=True))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.game_box = QComboBox()
        self.game_box.setEditable(True)
        self.game_box.addItems(games)
        self.game_box.setCurrentText(game)
        self.game_box.lineEdit().setPlaceholderText(tr("游戏名"))
        self.platform_box = QComboBox()
        self.platform_box.setEditable(True)
        self.platform_box.addItems(platforms)
        self.platform_box.setCurrentText(platform or "Switch")
        dur = QHBoxLayout()
        self.hours = QSpinBox()
        self.hours.setRange(0, 99999)
        self.hours.setSuffix(tr(" 小时"))
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.minutes.setSuffix(tr(" 分钟"))
        for w in (self.hours, self.minutes):
            w.setMinimumHeight(36)
            dur.addWidget(w)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setMinimumHeight(36)
        for w in (self.game_box, self.platform_box):
            w.setMinimumHeight(36)
        form.addRow(tr("游戏"), self.game_box)
        form.addRow(tr("平台"), self.platform_box)
        form.addRow(tr("时长"), dur)
        form.addRow(tr("最后游玩日期"), self.date)
        self.lay.addLayout(form)
        self.ok = _button(tr("添加"), "primary", self.accept_input)
        self.footer(_button(tr("取消"), slot=self.reject), self.ok)

    def accept_input(self):
        self.game = self.game_box.currentText().strip()
        self.platform = self.platform_box.currentText().strip()
        self.seconds = self.hours.value() * 3600 + self.minutes.value() * 60
        if not self.game or not self.platform or not self.seconds:
            self.set_status(tr("请填写游戏名、平台和时长。"), True)
            return
        d = self.date.date()
        today = d == QDate.currentDate()
        # end of that day (or now for today), so the time lands on the chosen date
        self.end = time.time() if today else time.mktime((d.year(), d.month(), d.day(), 22, 0, 0, 0, 0, -1))
        self.accept()


# --------------------------------------------------------------------------- cloud sync


class WebDavDialog(Dialog):
    """Guide for connecting 坚果云 (or any WebDAV); verifies the login before accepting."""

    def __init__(self, parent, url: str, user: str, run_async):
        from ..sync import JIANGUOYUN, JIANGUOYUN_SECURITY, JIANGUOYUN_SITE
        super().__init__(parent, tr("连接坚果云"), 660)
        self.run_async = run_async
        self.result = None  # (url, user, password) once verified
        self.lay.addWidget(label(tr("连接坚果云"), "pageTitle"))
        self.lay.addWidget(label(tr("游玩记录会保存在你自己的坚果云里，在多台电脑之间自动同步。坚果云免费版的流量对 PlayClock "
                                    "来说绰绰有余。只需 3 步。"), "muted", wrap=True))
        self.lay.addWidget(Step(1, tr("登录坚果云网页版"), tr("还没有账号的话可以免费注册。"),
                                _button(tr("打开坚果云"), slot=lambda: QDesktopServices.openUrl(QUrl(JIANGUOYUN_SITE)))))
        self.lay.addWidget(Step(2, tr("生成应用密码"),
                                tr("点右上角的账户名 → 账户信息 → 安全选项 → 第三方应用管理 → 添加应用，"
                                   "名称填 PlayClock，生成一个「应用密码」。注意：这里不能填坚果云的登录密码。"),
                                _button(tr("打开安全选项"),
                                        slot=lambda: QDesktopServices.openUrl(QUrl(JIANGUOYUN_SECURITY)))))
        step3 = Step(3, tr("填写账号和应用密码"), tr("密码会加密保存在这台电脑上。"))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.user = QLineEdit(user)
        self.user.setPlaceholderText(tr("坚果云登录邮箱或手机号"))
        self.password = QLineEdit()
        self.password.setPlaceholderText(tr("应用密码"))
        self.password.setEchoMode(QLineEdit.Password)
        self.url = QLineEdit(url or JIANGUOYUN)
        for w in (self.user, self.password, self.url):
            w.setMinimumHeight(36)
        form.addRow(tr("账号"), self.user)
        form.addRow(tr("应用密码"), self.password)
        form.addRow(tr("服务器"), self.url)
        step3.body.addLayout(form)
        self.lay.addWidget(step3)
        self.lay.addWidget(label(tr("使用其他 WebDAV 网盘（NAS、Nextcloud 等）时，把服务器改成它的 WebDAV 地址即可。"),
                                 "faint", wrap=True))
        self.ok = _button(tr("验证并保存"), "primary", self.verify)
        self.footer(_button(tr("取消"), slot=self.reject), self.ok)

    def verify(self):
        from ..sync import SyncError, WebDAV
        url, user, password = self.url.text().strip(), self.user.text().strip(), self.password.text()
        if not (url and user and password):
            self.set_status(tr("请填写账号和应用密码。"), True)
            return
        self.ok.setEnabled(False)
        self.set_status(tr("正在验证…"))

        def done(_result, error):
            if not isValid(self):
                return
            self.ok.setEnabled(True)
            if error is None:
                self.result = (url, user, password)
                self.accept()
            elif isinstance(error, SyncError) and str(error) == "auth":
                self.set_status(tr("坚果云账号或应用密码不正确。"), True)
            elif isinstance(error, SyncError) and str(error).startswith("network"):
                self.set_status(tr("无法连接服务器，请检查网络和服务器地址。"), True)
            else:
                self.set_status(tr("连接失败：{e}", e=error), True)

        self.run_async(lambda: WebDAV(url, user, password).check(), done)
