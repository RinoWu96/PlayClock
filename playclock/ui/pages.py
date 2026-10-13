"""The pages shown in the main window's content area."""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import theme
from ..i18n import tr, weekday
from .widgets import (
    BarChart, Card, Cover, FlowLayout, GameCard, NowPlaying, RankBars, SegmentBar, StatTile, Toggle,
    fmt_ago, fmt_date, fmt_dur, icon, label,
)

MINIPROGRAM_QR = Path(__file__).resolve().parent.parent / "assets" / "miniprogram_qr.png"  # bundled by build.bat


def phone_card(qr_path: Path = MINIPROGRAM_QR) -> Card:
    """Settings card pointing to the WeChat mini program; a placeholder until its code image is bundled."""
    card = Card(spacing=12)
    card.lay.addWidget(label(tr("手机查看"), "sectionTitle"))
    row = QHBoxLayout()
    row.setSpacing(18)
    qr = QLabel()
    qr.setObjectName("miniprogramQr")
    qr.setFixedSize(132, 132)
    qr.setAlignment(Qt.AlignCenter)
    pix = QPixmap(str(qr_path)) if qr_path.exists() else QPixmap()
    if pix.isNull():
        qr.setText(tr("小程序码\n即将上线"))
        qr.setStyleSheet(f"border: 1px dashed {theme.FAINT}; border-radius: 10px; color: {theme.MUTED};")
    else:
        qr.setPixmap(pix.scaled(132, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    row.addWidget(qr, 0, Qt.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(6)
    text.addWidget(label(tr("微信扫码打开 PlayClock 小程序"), wrap=True))
    text.addWidget(label(tr("在手机上查看正在玩的游戏、每日时长、游戏库和封面。先在上面开启云同步，"
                            "再在小程序的「设置」里填同一个坚果云账号和应用密码（建议另外生成一个）。"), "muted", wrap=True))
    text.addWidget(label(tr("小程序只读取坚果云里 PlayClock 文件夹的汇总文件，不修改任何数据。"), "faint", wrap=True))
    text.addStretch()
    row.addLayout(text, 1)
    card.lay.addLayout(row)
    return card


def midnight(days_ago: int = 0) -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - days_ago, 0, 0, 0, 0, 0, -1))


def week_start() -> float:
    return midnight(time.localtime().tm_wday)


def month_start() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def platforms_of(ctx, emulators: str | None) -> list[str]:
    """Distinct platforms of a game's comma-joined emulator list, in order."""
    out = []
    for e in (emulators or "").split(","):
        p = ctx.platform_of(e)
        if e and p not in out:
            out.append(p)
    return out


def played_badge(emulators: str | None) -> str:
    """Cover badge: how the game was played — the emulator (Ryujinx), Steam, or a hand-entered platform (Switch, PC)."""
    return " · ".join(dict.fromkeys(e for e in (emulators or "").split(",") if e))


def daily_points(rows):
    """db.daily rows -> BarChart data."""
    out = []
    for d, s in rows:
        t = time.strptime(d, "%Y-%m-%d")
        out.append((f"{t.tm_mon}/{t.tm_mday}", f"{d}  {weekday(t.tm_wday)}", s))
    return out


class Page(QScrollArea):
    """Scrollable page with a title header."""

    def __init__(self, ctx, title: str, subtitle: str = ""):
        super().__init__()
        self.ctx = ctx
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("content")
        self.setWidget(body)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(36, 30, 36, 36)
        self.lay.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label(title, "pageTitle")
        titles.addWidget(self.title)
        self.subtitle = label(subtitle, "pageSub")
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch()
        self.head = head
        self.lay.addLayout(head)

    def section(self, text: str):
        lb = label(text, "sectionTitle")
        self.lay.addWidget(lb)
        return lb

    def refresh(self):
        pass

    def on_tick(self):
        pass


def make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(42)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setFocusPolicy(Qt.NoFocus)
    t.horizontalHeader().setHighlightSections(False)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    t.setContextMenuPolicy(Qt.CustomContextMenu)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return t


def fill_sessions(t: QTableWidget, rows, platform_label, with_game: bool = True):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        cells = ([r["game"]] if with_game else []) + [
            platform_label(r["emulator"]), fmt_date(r["start"]), fmt_date(r["end"]), fmt_dur(r["seconds"])]
        for j, text in enumerate(cells):
            it = QTableWidgetItem(text)
            it.setData(Qt.UserRole, r["id"])
            if j == len(cells) - 1:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setForeground(Qt.white)
            t.setItem(i, j, it)
    # Let the page scroll instead of the table.
    t.setFixedHeight(t.horizontalHeader().height() + 42 * len(rows) + 4 if rows else 120)


# --------------------------------------------------------------------------- home


class HomePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, tr("概览"), "")
        self.banner = Card(padding=16, spacing=0)
        self.banner.setStyleSheet(f"#card {{ border-color: {theme.ACCENT}; }}")
        b_row = QHBoxLayout()
        b_row.setSpacing(12)
        self.banner_text = label("", None, wrap=True)
        b_row.addWidget(self.banner_text, 1)
        b_import = QPushButton(tr("导入"))
        b_import.setObjectName("primary")
        b_import.setCursor(Qt.PointingHandCursor)
        b_import.clicked.connect(lambda: ctx.open_import(self.banner_kind))
        b_dismiss = QPushButton(tr("不再提示"))
        b_dismiss.setObjectName("ghost")
        b_dismiss.setCursor(Qt.PointingHandCursor)
        b_dismiss.clicked.connect(self.dismiss_banner)
        b_row.addWidget(b_import)
        b_row.addWidget(b_dismiss)
        self.banner.lay.addLayout(b_row)
        self.banner.hide()
        self.lay.addWidget(self.banner)

        self.hero = NowPlaying()
        self.hero.setCursor(Qt.PointingHandCursor)
        self.hero.mouseReleaseEvent = lambda e: ctx.current_game() and ctx.open_game(ctx.current_game())
        self.lay.addWidget(self.hero)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_today = StatTile(tr("今天"), theme.ACCENT)
        self.t_week = StatTile(tr("本周"), theme.ACCENT_2)
        self.t_month = StatTile(tr("本月"), theme.GREEN)
        self.t_all = StatTile(tr("累计"), "#ffb547")
        for t in (self.t_today, self.t_week, self.t_month, self.t_all):
            tiles.addWidget(t)
        self.lay.addLayout(tiles)

        row = QHBoxLayout()
        row.addWidget(label(tr("最近游玩"), "sectionTitle"))
        row.addStretch()
        more = QPushButton(tr("查看全部游戏"))
        more.setObjectName("ghost")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: ctx.go("library"))
        row.addWidget(more)
        self.lay.addLayout(row)
        self.recent_box = QWidget()
        self.recent = QHBoxLayout(self.recent_box)
        self.recent.setContentsMargins(0, 0, 0, 0)
        self.recent.setSpacing(16)
        self.lay.addWidget(self.recent_box)

        chart_card = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("近 14 天"), "sectionTitle"))
        head.addStretch()
        self.chart_sum = label("", "muted")
        head.addWidget(self.chart_sum)
        chart_card.lay.addLayout(head)
        self.chart = BarChart(210)
        chart_card.lay.addWidget(self.chart)
        self.lay.addWidget(chart_card)
        self.lay.addStretch()

    def greeting(self):
        h = time.localtime().tm_hour
        return tr("夜深了" if h < 5 else "早上好" if h < 11 else "中午好" if h < 13 else "下午好" if h < 18 else "晚上好")

    def refresh(self):
        db = self.ctx.db
        self.title.setText(self.greeting())
        self.on_tick()
        self.update_banner()
        today, week, month = db.total_since(midnight()), db.total_since(week_start()), db.total_since(month_start())
        stats = db.game_stats()
        total = sum(r["total"] for r in stats)
        yesterday = db.total_since(midnight(1)) - today
        diff = today - yesterday
        self.t_today.set(fmt_dur(today), tr("比昨天多 {t}" if diff >= 0 else "比昨天少 {t}", t=fmt_dur(abs(diff))))
        self.t_week.set(fmt_dur(week), tr("日均 {t}", t=fmt_dur(week / (time.localtime().tm_wday + 1))))
        self.t_month.set(fmt_dur(month), tr("日均 {t}", t=fmt_dur(month / time.localtime().tm_mday)))
        self.t_all.set(fmt_dur(total), tr("{n} 款游戏", n=len(stats)))

        while self.recent.count():
            w = self.recent.takeAt(0).widget()
            if w:
                w.deleteLater()
        recent = sorted(stats, key=lambda r: r["last"] or 0, reverse=True)[:8]
        playing = self.ctx.current_game()
        for r in recent:
            card = GameCard(r["game"], played_badge(r["emulators"]), r["total"], r["last"],
                            self.ctx.cover(r["game"]), r["game"] == playing)
            card.clicked.connect(self.ctx.open_game)
            card.menu_requested.connect(self.ctx.game_menu)
            self.recent.addWidget(card)
        if not recent:
            self.recent.addWidget(label(tr("还没有任何记录。启动模拟器玩一会儿，这里就会出现你的游戏。"), "muted"))
        self.recent.addStretch()
        self.fit_recent()
        days = db.daily(14)
        self.chart.set_data(daily_points(days))
        s = sum(x for _, x in days)
        self.chart_sum.setText(tr("合计 {t} · 日均 {a}", t=fmt_dur(s), a=fmt_dur(s / 14)))

    def update_banner(self):
        tip = self.ctx.import_suggestion()
        self.banner.setVisible(bool(tip))
        if tip:
            self.banner_text.setText(tip[0])
            self.banner_kind = tip[1]

    def dismiss_banner(self):
        self.ctx.set_cfg(f"{self.banner_kind}_banner_dismissed", True)
        self.update_banner()

    def fit_recent(self):
        """Show only as many recent cards as fit on one row."""
        room = self.viewport().width() - 72
        n = max(1, (room + 16) // (GameCard.W + 16))
        shown = 0
        for i in range(self.recent.count()):
            w = self.recent.itemAt(i).widget()
            if isinstance(w, GameCard):
                w.setVisible(shown < n)
                shown += 1

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit_recent()


    def on_tick(self):
        cur = self.ctx.tracker.current
        if cur:
            d = self.ctx.db.game_detail(cur.game)
            self.hero.set(cur.game, cur.emulator, cur.seconds, d["total"] if d else cur.seconds, self.ctx.cover(cur.game))
        else:
            self.hero.set(None)


# --------------------------------------------------------------------------- library


class LibraryPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, tr("游戏库"))
        self.platform = None  # None = all
        add = QPushButton("+  " + tr("添加时长"))
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(lambda: ctx.add_time())
        self.head.addWidget(add)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索游戏"))
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setFixedWidth(260)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.sort = SegmentBar([(tr("最多时长"), "total"), (tr("最近游玩##sort"), "last"), (tr("名称"), "name")])
        self.sort.changed.connect(lambda _: self.refresh())
        self.head.addWidget(self.search)
        self.head.addWidget(self.sort)
        self.chips_box = QHBoxLayout()
        self.chips_box.addStretch()
        self.chips = None
        self.lay.addLayout(self.chips_box)
        self.grid_box = QWidget()
        self.grid = FlowLayout(self.grid_box, 16)
        self.lay.addWidget(self.grid_box)
        self.empty = label("", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.lay.addWidget(self.empty)
        self.lay.addStretch()

    def refresh(self):
        rows = [dict(r, platforms=platforms_of(self.ctx, r["emulators"])) for r in self.ctx.db.game_stats()]
        self.build_chips(rows)
        if self.platform:
            rows = [r for r in rows if self.platform in r["platforms"]]
        q = self.search.text().strip().lower()
        if q:
            rows = [r for r in rows if q in r["game"].lower()]
        key = self.sort.current()
        if key == "last":
            rows.sort(key=lambda r: r["last"] or 0, reverse=True)
        elif key == "name":
            rows.sort(key=lambda r: r["game"].lower())
        total = sum(r["total"] for r in rows)
        self.subtitle.setText(tr("{n} 款游戏 · 合计 {t}", n=len(rows), t=fmt_dur(total)))
        self.grid.clear()
        playing = self.ctx.current_game()
        for r in rows:
            card = GameCard(r["game"], played_badge(r["emulators"]), r["total"], r["last"],
                            self.ctx.cover(r["game"]), r["game"] == playing)
            card.clicked.connect(self.ctx.open_game)
            card.menu_requested.connect(self.ctx.game_menu)
            self.grid.addWidget(card)
        self.empty.setVisible(not rows)
        self.empty.setText(tr("没有匹配的游戏" if q else "还没有记录到任何游戏"))
        self.grid_box.updateGeometry()

    def build_chips(self, rows):
        """One filter button per platform that has games, busiest first."""
        counts: dict[str, int] = {}
        for r in rows:
            for p in r["platforms"]:
                counts[p] = counts.get(p, 0) + 1
        if self.platform not in counts:
            self.platform = None
        options = [(f"{tr('全部')}  {len(rows)}", None)] + \
            [(f"{p}  {n}", p) for p, n in sorted(counts.items(), key=lambda kv: -kv[1])]
        if self.chips:
            self.chips.deleteLater()
        self.chips = SegmentBar(options, self.platform) if self.platform else SegmentBar(options)
        self.chips.changed.connect(self.pick_platform)
        self.chips_box.insertWidget(0, self.chips)
        self.chips.setVisible(len(counts) > 1)

    def pick_platform(self, p):
        self.platform = p
        QTimer.singleShot(0, self.refresh)  # rebuild after the click finishes


# --------------------------------------------------------------------------- game detail


class GamePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "")
        self.game = None
        back = QPushButton("  " + tr("返回"))
        back.setObjectName("ghost")
        back.setIcon(icon("back"))
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(ctx.back)
        self.lay.insertWidget(0, back, 0, Qt.AlignLeft)
        self.title.hide()
        self.subtitle.hide()

        top = QHBoxLayout()
        top.setSpacing(28)
        self.cover = Cover(384, 216, 16)
        top.addWidget(self.cover)
        info = QVBoxLayout()
        info.setSpacing(8)
        self.emu = label("", "muted")
        self.name = label("", "pageTitle", wrap=True)
        self.big = label("", "tileValue")
        self.big.setStyleSheet(f"font-size: 26pt; color: {theme.TEXT};")
        self.big_sub = label("", "muted")
        info.addWidget(self.emu)
        info.addWidget(self.name)
        info.addSpacing(6)
        info.addWidget(self.big)
        info.addWidget(self.big_sub)
        info.addStretch()
        btns = QHBoxLayout()
        btns.setSpacing(10)
        b_online = QPushButton(tr("在线搜索封面"))
        b_online.setObjectName("primary")
        b_online.clicked.connect(lambda: ctx.search_cover(self.game))
        b_cover = QPushButton(tr("本地图片"))
        b_cover.clicked.connect(lambda: ctx.choose_cover(self.game))
        self.b_clear = QPushButton(tr("移除封面"))
        self.b_clear.clicked.connect(lambda: ctx.clear_cover(self.game))
        b_rename = QPushButton(tr("重命名 / 合并"))
        b_rename.clicked.connect(lambda: ctx.rename_game(self.game))
        b_add = QPushButton(tr("添加时长"))
        b_add.clicked.connect(lambda: ctx.add_time(self.game, self.default_platform()))
        b_del = QPushButton(tr("删除记录"))
        b_del.setObjectName("danger")
        b_del.clicked.connect(lambda: ctx.delete_game(self.game))
        for b in (b_online, b_cover, self.b_clear, b_rename, b_add, b_del):
            b.setCursor(Qt.PointingHandCursor)
            btns.addWidget(b)
        btns.addStretch()
        info.addLayout(btns)
        top.addLayout(info, 1)
        self.lay.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_n = StatTile(tr("游玩次数"), theme.ACCENT)
        self.t_avg = StatTile(tr("平均每次"), theme.ACCENT_2)
        self.t_first = StatTile(tr("首次游玩"), theme.GREEN)
        self.t_last = StatTile(tr("最近游玩##last"), "#ffb547")
        for t in (self.t_n, self.t_avg, self.t_first, self.t_last):
            tiles.addWidget(t)
        self.lay.addLayout(tiles)

        chart_card = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("每日时长"), "sectionTitle"))
        head.addStretch()
        self.range = SegmentBar([(tr("{n} 天", n=30), 30), (tr("{n} 天", n=90), 90)])
        self.range.changed.connect(lambda _: self.refresh())
        head.addWidget(self.range)
        chart_card.lay.addLayout(head)
        self.chart = BarChart(200)
        chart_card.lay.addWidget(self.chart)
        self.lay.addWidget(chart_card)

        self.section(tr("游玩记录"))
        self.table = make_table([tr("平台"), tr("开始"), tr("结束"), tr("时长")])
        self.table.customContextMenuRequested.connect(
            lambda pos: ctx.session_menu(self.table, pos))
        self.lay.addWidget(self.table)
        self.lay.addStretch()

    def default_platform(self):
        d = self.ctx.db.game_detail(self.game)
        return self.ctx.platform_of(d["emulators"].split(",")[0]) if d and d["emulators"] else ""

    def show_game(self, game):
        self.game = game
        self.verticalScrollBar().setValue(0)
        self.refresh()

    def refresh(self):
        if not self.game:
            return
        db = self.ctx.db
        d = db.game_detail(self.game)
        if not d or not d["n"]:
            self.ctx.back()
            return
        self.cover.set(self.game, self.ctx.cover(self.game))
        self.b_clear.setVisible(bool(db.get_cover(self.game)))
        playing = self.ctx.current_game() == self.game
        self.emu.setText((tr("● 正在游玩  ·  ") if playing else "")
                         + " / ".join(self.ctx.platform_label(e) for e in d["emulators"].split(",")))
        self.emu.setStyleSheet(f"color: {theme.GREEN if playing else theme.MUTED};")
        self.name.setText(self.game)
        self.big.setText(fmt_dur(d["total"]))
        self.big_sub.setText(tr("总游玩时长"))
        self.t_n.set(tr("{n} 次", n=d["n"]))
        self.t_avg.set(fmt_dur(d["total"] / d["n"]))
        self.t_first.set(fmt_date(d["first"], False), fmt_ago(d["first"]))
        self.t_last.set(fmt_date(d["last"], False), fmt_ago(d["last"]))
        days = self.range.current()
        self.chart.set_data(daily_points(db.daily(days, self.game)))
        fill_sessions(self.table, db.sessions(self.game), self.ctx.platform_label, with_game=False)

    def on_tick(self):
        if self.game and self.ctx.current_game() == self.game:
            d = self.ctx.db.game_detail(self.game)
            if d and d["n"]:
                self.big.setText(fmt_dur(d["total"]))


# --------------------------------------------------------------------------- history


class HistoryPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, tr("历史记录"), tr("每一次游玩的详细时间"))
        self.filter = QComboBox()
        self.filter.setMinimumWidth(260)
        self.filter.currentIndexChanged.connect(lambda _: self.refresh_table())
        self.head.addWidget(self.filter)
        self.table = make_table([tr("游戏"), tr("平台"), tr("开始"), tr("结束"), tr("时长")])
        self.table.customContextMenuRequested.connect(lambda pos: ctx.session_menu(self.table, pos))
        self.table.cellDoubleClicked.connect(lambda r, _: ctx.open_game(self.table.item(r, 0).text()))
        self.lay.addWidget(self.table)
        self.lay.addStretch()

    def refresh(self):
        cur = self.filter.currentText()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem(tr("全部游戏"))
        self.filter.addItems(self.ctx.db.games())
        self.filter.setCurrentIndex(max(0, self.filter.findText(cur)))
        self.filter.blockSignals(False)
        self.refresh_table()

    def refresh_table(self):
        game = self.filter.currentText() if self.filter.currentIndex() > 0 else None
        rows = self.ctx.db.sessions(game, limit=500)
        fill_sessions(self.table, rows, self.ctx.platform_label)
        self.subtitle.setText(tr("显示最近 {n} 条", n=len(rows)) + ("" if len(rows) < 500 else tr("（最多 500 条，可导出 CSV 查看全部）")))


# --------------------------------------------------------------------------- stats


class StatsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, tr("统计"))
        ranges = [(tr("近 {n} 天", n=n), n) for n in (7, 30, 90)] + [(tr("近 12 个月"), 365), (tr("全部"), 0)]
        self.range = SegmentBar(ranges, 30)
        self.range.changed.connect(lambda _: self.refresh())
        self.head.addWidget(self.range)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_total = StatTile(tr("总时长"), theme.ACCENT)
        self.t_avg = StatTile(tr("日均"), theme.ACCENT_2)
        self.t_days = StatTile(tr("游玩天数"), theme.GREEN)
        self.t_games = StatTile(tr("游戏数"), "#ffb547")
        for t in (self.t_total, self.t_avg, self.t_days, self.t_games):
            tiles.addWidget(t)
        self.lay.addLayout(tiles)

        trend = Card()
        self.trend_title = label(tr("每日时长"), "sectionTitle")
        trend.lay.addWidget(self.trend_title)
        self.chart = BarChart(230)
        trend.lay.addWidget(self.chart)
        self.trend_note = label(tr("从 Steam 等平台导入的历史时长没有具体日期，只计入总时长和排行，不计入趋势、日均和游玩天数。"),
                                "faint", wrap=True)
        trend.lay.addWidget(self.trend_note)
        self.lay.addWidget(trend)

        row = QHBoxLayout()
        row.setSpacing(16)
        games = Card()
        games.lay.addWidget(label(tr("游戏排行"), "sectionTitle"))
        self.rank = RankBars(clickable=True)
        self.rank.clicked.connect(ctx.open_game)
        games.lay.addWidget(self.rank)
        games.lay.addStretch()
        emus = Card()
        emus.lay.addWidget(label(tr("平台"), "sectionTitle"))
        self.emu_rank = RankBars(colors=[theme.ACCENT_2, theme.GREEN, "#ffb547", "#4cc9f0", "#ff6b9a"])
        emus.lay.addWidget(self.emu_rank)
        emus.lay.addStretch()
        row.addWidget(games, 3)
        row.addWidget(emus, 2)
        self.lay.addLayout(row)
        self.lay.addStretch()

    def refresh(self):
        db = self.ctx.db
        days = self.range.current()
        if days == 365 or days == 0:
            rows = db.monthly(12 if days else self._months_all())
            since = 0 if days == 0 else time.mktime(time.strptime(rows[0][0] + "-01", "%Y-%m-%d"))
            self.trend_title.setText(tr("每月时长"))
            self.chart.set_data([(m[2:].replace("-", "/"), m, s) for m, s in rows])
            span_days = max(1, (time.time() - (since or self._first())) / 86400)
        else:
            rows = db.daily(days)
            since = midnight(days - 1)
            self.trend_title.setText(tr("每日时长"))
            self.chart.set_data(daily_points(rows))
            span_days = days
        stats = db.game_stats(since)
        total = sum(r["total"] for r in stats)
        played_days = db.played_days(since)
        self.t_total.set(fmt_dur(total))
        self.t_avg.set(fmt_dur(db.total_since(since) / span_days))  # dated time only
        self.trend_note.setVisible(db.has_imports())
        self.t_days.set(tr("{n} 天", n=played_days))
        self.t_games.set(tr("{n} 款", n=len(stats)))
        self.rank.set_rows([(r["game"], r["total"]) for r in stats[:10]])
        per_platform: dict[str, float] = {}
        for r in db.emulator_stats(since):
            p = self.ctx.platform_of(r["emulator"])
            per_platform[p] = per_platform.get(p, 0) + r["total"]
        self.emu_rank.set_rows(sorted(per_platform.items(), key=lambda kv: -kv[1]))

    def _first(self) -> float:
        return self.ctx.db.first_dated() or time.time()

    def _months_all(self) -> int:
        a, b = time.localtime(self._first()), time.localtime()
        return max(1, (b.tm_year - a.tm_year) * 12 + b.tm_mon - a.tm_mon + 1)


# --------------------------------------------------------------------------- settings


class SettingsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, tr("设置"))
        cfg = ctx.cfg

        general = Card(spacing=16)
        general.lay.addWidget(label(tr("常规"), "sectionTitle"))
        lang_row = QHBoxLayout()
        lang_row.addWidget(label(tr("语言") if tr("语言") == "Language" else tr("语言") + "  ·  Language"))
        lang_row.addSpacing(12)
        self.lang = SegmentBar([(tr("跟随系统"), "auto"), ("中文", "zh"), ("English", "en")],
                               cfg.get("language", "auto"))
        self.lang.changed.connect(ctx.set_language)
        lang_row.addWidget(self.lang)
        lang_row.addStretch()
        general.lay.addLayout(lang_row)
        self.autostart = Toggle(tr("开机时自动启动（最小化到托盘）"))
        self.autostart.setChecked(ctx.get_autostart())
        self.autostart.toggled.connect(ctx.set_autostart)
        general.lay.addWidget(self.autostart)
        self.fg = Toggle(tr("只在模拟器窗口位于前台时计时"))
        self.fg.setChecked(bool(cfg["foreground_only"]))
        self.fg.toggled.connect(lambda v: ctx.set_cfg("foreground_only", v))
        general.lay.addWidget(self.fg)
        general.lay.addWidget(label(tr("模拟器窗口最小化时不计时。"), "faint"))
        self.lay.addWidget(general)

        timing = Card(spacing=14)
        timing.lay.addWidget(label(tr("计时"), "sectionTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        def spin(key, lo, hi, on_change=None):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSuffix(tr(" 秒"))
            s.setFixedSize(140, 38)
            s.setValue(int(cfg[key]))
            s.valueChanged.connect(on_change or (lambda v: ctx.set_cfg(key, v)))
            return s

        def row_label(text):
            lb = label(text)
            lb.setFixedHeight(38)  # same as the spin boxes so the text centres on them
            return lb

        form.addRow(row_label(tr("检测间隔")), spin("interval", 1, 60, ctx.set_interval))
        form.addRow(row_label(tr("短于此时长的游玩不记录")), spin("min_session", 0, 3600))
        form.addRow(row_label(tr("中断多久内重开算同一次")), spin("resume_gap", 0, 3600))
        timing.lay.addLayout(form)
        timing.lay.addWidget(label(tr("电脑睡眠或休眠期间的时间不会被计入。"), "faint"))
        self.lay.addWidget(timing)

        detect = Card(spacing=12)
        head = QHBoxLayout()
        head.addWidget(label(tr("识别"), "sectionTitle"))
        head.addStretch()
        for text, fn in ((tr("自定义规则"), ctx.edit_rules), (tr("重新加载规则"), ctx.reload_rules)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            head.addWidget(b)
        detect.lay.addLayout(head)
        self.rules_label = label("", "muted", wrap=True)
        detect.lay.addWidget(self.rules_label)
        detect.lay.addWidget(label(tr("当前检测到的模拟器窗口（游戏名识别不准时，可对照这里的标题修改规则）："), "faint", wrap=True))
        self.debug = QTextEdit()
        self.debug.setReadOnly(True)
        self.debug.setFixedHeight(150)
        self.debug.setStyleSheet("font-family: Consolas, 'Microsoft YaHei UI'; font-size: 9pt;")
        detect.lay.addWidget(self.debug)
        self.lay.addWidget(detect)

        pc = Card(spacing=10)
        head = QHBoxLayout()
        head.addWidget(label(tr("PC 游戏"), "sectionTitle"))
        head.addStretch()
        for text, fn in ((tr("从正在运行的程序添加…"), ctx.add_running_pc_game), (tr("选择 exe…"), ctx.add_pc_game)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, f=fn: f())
            head.addWidget(b)
        pc.lay.addLayout(head)
        pc.lay.addWidget(label(tr("不在 Steam 里的电脑游戏：添加它的 exe 后，运行时会自动计时，平台记为 PC。"
                                  "Steam 游戏请用「从 Steam 导入」，不要在这里添加，否则会重复计算。"), "faint", wrap=True))
        self.pc_list = QVBoxLayout()
        self.pc_list.setSpacing(6)
        pc.lay.addLayout(self.pc_list)
        self.lay.addWidget(pc)

        covers = Card(spacing=12)
        c_head = QHBoxLayout()
        c_head.addWidget(label(tr("封面"), "sectionTitle"))
        c_head.addStretch()
        self.b_connect = QPushButton()
        self.b_connect.setCursor(Qt.PointingHandCursor)
        self.b_connect.clicked.connect(ctx.connect_sgdb)
        self.b_disconnect = QPushButton(tr("断开"))
        self.b_disconnect.setObjectName("ghost")
        self.b_disconnect.setCursor(Qt.PointingHandCursor)
        self.b_disconnect.clicked.connect(ctx.disconnect_sgdb)
        c_head.addWidget(self.b_disconnect)
        c_head.addWidget(self.b_connect)
        covers.lay.addLayout(c_head)
        self.sgdb_state = label("", "muted", wrap=True)
        covers.lay.addWidget(self.sgdb_state)
        self.auto_cover = Toggle(tr("为新识别到的游戏自动下载封面"))
        self.auto_cover.setChecked(bool(cfg.get("auto_cover", True)))
        self.auto_cover.toggled.connect(lambda v: ctx.set_cfg("auto_cover", v))
        covers.lay.addWidget(self.auto_cover)
        c_row = QHBoxLayout()
        self.b_fill = QPushButton(tr("为没有封面的游戏补全封面"))
        self.b_fill.setCursor(Qt.PointingHandCursor)
        self.b_fill.clicked.connect(ctx.fill_missing_covers)
        c_row.addWidget(self.b_fill)
        c_row.addStretch()
        covers.lay.addLayout(c_row)
        self.lay.addWidget(covers)

        history = Card(spacing=12)
        history.lay.addWidget(label(tr("导入历史时长"), "sectionTitle"))
        history.lay.addWidget(label(tr("把模拟器自己记录的游玩时长导入 PlayClock，安装之前玩的时间也能算进统计。"
                                       "只补上 PlayClock 没记录到的部分，可以放心重复导入。"), "muted", wrap=True))
        h_row = QHBoxLayout()
        for text, kind in ((tr("从 Steam 导入…"), "steam"), (tr("从 Ryujinx 导入…"), "ryujinx")):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=kind: ctx.open_import(k))
            h_row.addWidget(b)
        h_row.addStretch()
        history.lay.addLayout(h_row)
        self.steam_state = label("", "faint", wrap=True)
        history.lay.addWidget(self.steam_state)
        self.lay.addWidget(history)

        cloud = Card(spacing=12)
        s_head = QHBoxLayout()
        s_head.addWidget(label(tr("云同步"), "sectionTitle"))
        s_head.addStretch()
        self.b_sync_off = QPushButton(tr("停止同步"))
        self.b_sync_off.setObjectName("ghost")
        self.b_sync_off.clicked.connect(ctx.disconnect_webdav)
        self.b_sync_now = QPushButton(tr("立即同步"))
        self.b_sync_now.clicked.connect(ctx.sync_now)
        self.b_sync_setup = QPushButton()
        self.b_sync_setup.clicked.connect(ctx.connect_webdav)
        for b in (self.b_sync_off, self.b_sync_now, self.b_sync_setup):
            b.setCursor(Qt.PointingHandCursor)
            s_head.addWidget(b)
        cloud.lay.addLayout(s_head)
        self.sync_state = label("", "muted", wrap=True)
        cloud.lay.addWidget(self.sync_state)
        cloud.lay.addWidget(label(tr("通过你自己的坚果云（或任意 WebDAV 网盘）在多台电脑之间同步游玩记录，"
                                     "不经过任何第三方服务器。封面图暂不同步，每台设备会各自补全。"), "faint", wrap=True))
        self.lay.insertWidget(2, cloud)  # right after "General": header layout is 0, general card 1
        self.lay.insertWidget(3, phone_card())

        data = Card(spacing=12)
        data.lay.addWidget(label(tr("数据"), "sectionTitle"))
        self.data_label = label("", "muted", wrap=True)
        self.data_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data.lay.addWidget(self.data_label)
        row = QHBoxLayout()
        for text, fn in ((tr("打开数据文件夹"), ctx.open_data), (tr("导出 CSV"), ctx.export_csv)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        data.lay.addLayout(row)
        self.lay.addWidget(data)

        about = Card(spacing=6)
        about.lay.addWidget(label(tr("关于"), "sectionTitle"))
        self.about = label("", "muted", wrap=True)
        about.lay.addWidget(self.about)
        self.lay.addWidget(about)
        self.lay.addStretch()

    def refresh(self):
        self.rules_label.setText(tr("支持：") + tr("、").join(r.name for r in self.ctx.tracker.rules)
                                 + tr("\n内置规则随软件版本更新；自己添加或修改的规则保存在 rules.user.json，更新时不会被覆盖。"))
        self.data_label.setText(tr("所有记录都只保存在本机：{path}", path=self.ctx.data_dir))
        self.refresh_sync()
        self.refresh_pc_games()
        acc = self.ctx.cfg.get("steam_sync")
        self.steam_state.setText(tr("Steam 自动同步：已开启（账号 {acc}），Steam 记录有变化时会自动更新。", acc=acc) if acc
                                 else tr("Steam 自动同步：未开启。导入 Steam 时可以选择开启。"))
        connected = bool(self.ctx.sgdb_key())
        self.sgdb_state.setText(tr("已连接 SteamGridDB，可以在游戏详情页在线搜索封面。") if connected else
                                tr("连接免费的 SteamGridDB 封面库后，可以在线搜索封面，并为新游戏自动配图。"))
        self.b_connect.setText(tr("更换 API Key") if connected else tr("连接 SteamGridDB…"))
        self.b_connect.setObjectName("" if connected else "primary")
        self.b_connect.style().polish(self.b_connect)
        self.b_disconnect.setVisible(connected)
        self.auto_cover.setEnabled(connected)
        self.b_fill.setEnabled(connected)
        self.about.setText(f"{self.ctx.app_title} v{self.ctx.version}\n"
                           + tr("通过读取模拟器窗口标题识别正在运行的游戏，不读取、不修改任何游戏文件。只有使用 SteamGridDB 封面功能时才会联网。"))
        self.on_tick()

    def refresh_sync(self):
        cfg = self.ctx.cfg
        on = bool(cfg.get("webdav"))
        if not on:
            text = tr("未开启。")
        elif self.ctx.ctrl.syncing:
            text = tr("正在同步…")
        elif cfg.get("sync_error"):
            text = tr("上次同步失败：{e}", e=cfg["sync_error"])
        elif cfg.get("last_sync"):
            others = [n for d, n in cfg.get("sync_devices", {}).items() if d != cfg.get("device_id")]
            text = tr("已连接 {user}，上次同步：{t}。", user=cfg["webdav"]["user"], t=fmt_ago(cfg["last_sync"]))
            if others:
                text += " " + tr("其他设备：{names}", names=tr("、").join(others))
        else:
            text = tr("已连接 {user}，等待首次同步。", user=cfg["webdav"]["user"])
        self.sync_state.setText(text)
        self.sync_state.setStyleSheet(f"color: {theme.DANGER if on and cfg.get('sync_error') else theme.MUTED};")
        self.b_sync_setup.setText(tr("修改账号") if on else tr("连接坚果云…"))
        self.b_sync_setup.setObjectName("" if on else "primary")
        self.b_sync_setup.style().polish(self.b_sync_setup)
        self.b_sync_now.setVisible(on)
        self.b_sync_now.setEnabled(not self.ctx.ctrl.syncing)
        self.b_sync_off.setVisible(on)

    def refresh_pc_games(self):
        while self.pc_list.count():
            item = self.pc_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        games = self.ctx.cfg.get("pc_games", [])
        if not games:
            self.pc_list.addWidget(label(tr("还没有添加 PC 游戏。"), "muted"))
        for g in games:
            row = QHBoxLayout()
            name = label(g["name"])
            name.setFixedWidth(200)
            name.setToolTip(g["name"])
            row.addWidget(name)
            path = label(g["path"], "faint")
            path.setToolTip(g["path"])
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(path, 1)
            b = QPushButton(tr("移除"))
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=g["path"]: self.ctx.remove_pc_game(p))
            row.addWidget(b)
            self.pc_list.addLayout(row)

    def on_tick(self):
        lines = []
        for emu, w in self.ctx.tracker.seen:
            flags = tr("  [前台]") if w.foreground else ""
            flags += tr("  [最小化]") if w.minimized else ""
            lines.append(f"{emu}  ·  {w.exe}{flags}\n    {w.title}")
        self.debug.setPlainText("\n".join(lines) or tr("（没有发现正在运行的已知模拟器）"))
