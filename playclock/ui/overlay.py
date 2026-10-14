"""Floating panel: a small always-on-top window showing what is being recorded right now.

It never takes keyboard focus (so clicking it doesn't leave the game), is themed from the current game's
cover, and only exists while shown — hiding it destroys it, so it costs nothing in the background.
Two layouts: a card (cover, game, status, timer, totals) and a slim bar (status light + timer only).
Colours: the cover's dominant colour as the background, or dark with the cover colour as an accent.
"""
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from ..i18n import fmt_dur, tr
from . import theme
from .widgets import fmt_clock, hue_of, paint_cover

_accents: dict[str, QColor] = {}


def accent_of(game: str, pixmap) -> QColor:
    """The cover's dominant vivid colour (hue buckets weighted by saturation x value), else a colour from the name."""
    key = f"{game}|{pixmap.cacheKey() if pixmap else 0}"
    if key in _accents:
        return _accents[key]
    color = QColor.fromHsl(hue_of(game), 150, 130)
    if pixmap and not pixmap.isNull():
        img = pixmap.toImage().scaled(40, 22).convertToFormat(QImage.Format_RGB32)
        weight = [0.0] * 12
        sums = [[0.0, 0.0, 0.0] for _ in range(12)]
        for y in range(img.height()):
            for x in range(img.width()):
                c = QColor(img.pixel(x, y))
                h, s, v = c.hsvHueF(), c.hsvSaturationF(), c.valueF()
                if h < 0 or s < 0.3 or v < 0.25:
                    continue
                b = int(h * 12) % 12
                w = s * v
                weight[b] += w
                sums[b][0] += c.redF() * w
                sums[b][1] += c.greenF() * w
                sums[b][2] += c.blueF() * w
        best = max(range(12), key=lambda i: weight[i])
        if weight[best] > 0:
            r, g, b = (v / weight[best] for v in sums[best])
            c = QColor.fromRgbF(r, g, b)
            color = QColor.fromHsvF(max(c.hsvHueF(), 0), max(c.hsvSaturationF(), 0.55), max(c.valueF(), 0.8))
    _accents[key] = color
    return color


def ink(bg: QColor) -> QColor:
    """Readable text colour on a background: near-black on light colours, white on dark ones."""
    lum = 0.299 * bg.redF() + 0.587 * bg.greenF() + 0.114 * bg.blueF()
    return QColor("#15171c") if lum > 0.6 else QColor("white")


def faded(c: QColor, alpha: int) -> QColor:
    c = QColor(c)
    c.setAlpha(alpha)
    return c


class Overlay(QWidget):
    W, H = 380, 124          # card
    BAR_W, BAR_H = 180, 34   # slim bar

    def __init__(self, ctrl):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.ctrl = ctrl
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("PlayClock")
        self.mode = ctrl.cfg.get("overlay_mode", "card")      # card | bar
        self.palette_mode = ctrl.cfg.get("overlay_theme", "cover")  # cover | dark
        self.drag: QPoint | None = None
        self.tip = ""
        self.button_ink = None  # text colour the buttons are currently styled for
        self.totals: tuple[str, float, float, float] | None = None  # (game, today, all time, when computed)

        self.buttons = {}
        for key, text, tip, slot in (
                ("add", "+", tr("选择正在运行的游戏"), self.pick_game),
                ("bar", "−", tr("缩小为横条（右键可切换模式和配色）"), lambda: self.set_mode("bar")),
                ("open", "⌂", tr("打开主界面"), lambda: ctrl.show_window()),
                ("hide", "×", tr("隐藏浮窗（Ctrl+Alt+P）"), lambda: ctrl.set_overlay(False))):
            b = QPushButton(text, self)
            b.setToolTip(tip)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            self.buttons[key] = b

        self.layout_mode()
        pos = ctrl.cfg.get("overlay_pos")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if pos and any(s.availableGeometry().contains(QPoint(*pos)) for s in QGuiApplication.screens()):
            self.move(*pos)
        else:
            self.move(screen.right() - self.width() - 24, screen.top() + 24)

        self.clock = QTimer(self)  # repaint once a second while shown
        self.clock.timeout.connect(self.update)
        self.clock.start(1000)

    # --- modes -------------------------------------------------------------
    def layout_mode(self):
        bar = self.mode == "bar"
        if bar:
            self.setFixedSize(self.BAR_W, self.BAR_H)
        else:
            self.setFixedSize(self.W, self.H)
        for key in ("bar", "open", "hide"):
            self.buttons[key].setVisible(not bar)
        if bar:
            self.buttons["add"].move(self.BAR_W - 29, 5)
        else:
            for i, key in enumerate(("hide", "open", "bar", "add")):
                self.buttons[key].move(self.W - 34 - i * 28, 10)

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        right = self.x() + self.width()            # keep the right edge where it was
        self.mode = mode
        self.layout_mode()
        self.move(right - self.width(), self.y())
        self.ctrl.set_cfg("overlay_mode", mode)
        self.ctrl.set_cfg("overlay_pos", [self.x(), self.y()])
        self.update()

    def style_buttons(self, text: QColor):
        """Round translucent buttons in the palette's text colour (restyled only when that colour changes)."""
        if text == self.button_ink:
            return
        self.button_ink = QColor(text)
        r, g, b = text.red(), text.green(), text.blue()
        css = (f"QPushButton {{ background: rgba({r},{g},{b},0.14); border: none; border-radius: 12px;"
               f" color: rgba({r},{g},{b},0.9); font-size: 13pt; padding: 0 0 2px 0; }}"
               f"QPushButton:hover {{ background: rgba({r},{g},{b},0.30); }}")
        for btn in self.buttons.values():
            btn.setStyleSheet(css)

    def set_palette(self, mode: str):
        self.palette_mode = mode
        self.ctrl.set_cfg("overlay_theme", mode)
        self.update()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        for text, mode in ((tr("卡片模式"), "card"), (tr("横条模式（只显示计时）"), "bar")):
            a = m.addAction(text, lambda mode=mode: self.set_mode(mode))
            a.setCheckable(True)
            a.setChecked(self.mode == mode)
        m.addSeparator()
        for text, mode in ((tr("配色：封面颜色"), "cover"), (tr("配色：深色"), "dark")):
            a = m.addAction(text, lambda mode=mode: self.set_palette(mode))
            a.setCheckable(True)
            a.setChecked(self.palette_mode == mode)
        m.addSeparator()
        m.addAction(tr("打开主界面"), lambda: self.ctrl.show_window())
        m.addAction(tr("隐藏浮窗（Ctrl+Alt+P）"), lambda: self.ctrl.set_overlay(False))
        m.exec(e.globalPos())

    def colors(self, game, pixmap) -> tuple[QLinearGradient, QColor, QColor, QColor]:
        """(background, border, text colour, accent for the timer) for the current palette."""
        r = QRectF(self.rect())
        accent = accent_of(game, pixmap) if game else QColor(theme.ACCENT)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if self.palette_mode == "cover" and game:
            top, bottom = accent.darker(112), accent.darker(190)
            g.setColorAt(0, faded(top, 242))
            g.setColorAt(1, faded(bottom, 242))
            text = ink(QColor.fromRgbF((top.redF() + bottom.redF()) / 2, (top.greenF() + bottom.greenF()) / 2,
                                       (top.blueF() + bottom.blueF()) / 2))
            return g, faded(accent.lighter(140), 170), text, text
        g.setColorAt(0, faded(accent.darker(420), 240))
        g.setColorAt(1, faded(QColor("#0e1014"), 240))
        return g, faded(accent, 140), QColor("white"), accent.lighter(130)

    # --- state -------------------------------------------------------------
    def state(self):
        """(kind, game, emulator, seconds): recording / paused / background / none"""
        t = self.ctrl.tracker
        cur = t.current
        if cur:
            extra = min(time.time() - t.last_tick, float(self.ctrl.cfg["interval"])) if t.last_tick else 0
            return "recording", cur.game, cur.emulator, cur.seconds + max(0.0, extra)
        paused = next((r.game for r in t.logs.values() if r.game and r.paused), None)
        if paused:
            return "paused", self.ctrl.db.resolve(paused), "Ryujinx", 0.0
        if t.detections:
            d = t.detections[0]
            return "background", self.ctrl.db.resolve(d.game), d.emulator, 0.0
        return "none", None, "", 0.0

    def game_totals(self, game: str) -> tuple[float, float]:
        """(today, all time) for the game, refreshed every 15 s rather than on every repaint."""
        now = time.time()
        if not self.totals or self.totals[0] != game or now - self.totals[3] > 15:
            db = self.ctrl.db
            today = sum(s for _, s in db.daily(1, game))
            d = db.game_detail(game)
            self.totals = (game, today, (d["total"] or 0) if d else 0, now)
        return self.totals[1], self.totals[2]

    # --- painting ------------------------------------------------------------
    STATUS_DOT = {"recording": theme.GREEN, "paused": "#ffb547", "background": theme.MUTED, "none": theme.FAINT}

    def paintEvent(self, _):
        kind, game, emulator, seconds = self.state()
        self.buttons["add"].setVisible(kind == "none")
        tip = game or tr("未识别到游戏")
        if tip != self.tip:
            self.tip = tip
            self.setToolTip(tip)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pixmap = self.ctrl.cover(game) if game else None
        bg, border, text, timer = self.colors(game, pixmap)
        self.style_buttons(text)

        path = QPainterPath()
        radius = r.height() / 2 if self.mode == "bar" else 16
        path.addRoundedRect(r, radius, radius)
        p.fillPath(path, bg)
        p.setPen(QPen(border, 1.2))
        p.drawPath(path)
        if self.mode == "bar":
            self.paint_bar(p, r, kind, seconds, text)
            return

        pad = 14
        if game:
            cover = QRectF(pad, pad, 128, 72)
            paint_cover(p, cover, game, pixmap, 10)
            x = cover.right() + 14
        else:
            x = pad + 4
        dot = QColor(self.STATUS_DOT[kind])
        status = {
            "recording": tr("记录中") + (f" · {emulator}" if emulator else ""),
            "paused": tr("游戏已暂停，不计时"),
            "background": tr("游戏不在前台，暂停计时"),
            "none": tr("未识别到游戏"),
        }[kind]
        p.setPen(QPen(faded(text, 120), 1))
        p.setBrush(dot)
        p.drawEllipse(QRectF(x, pad + 5, 8, 8))
        p.setPen(faded(text, 200))
        p.setFont(theme.font(9))
        p.drawText(QRectF(x + 14, pad - 1, self.W - x - 110, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(status, Qt.ElideRight, int(self.W - x - 110)))
        p.setPen(text)
        p.setFont(theme.font(11.5, QFont.Bold))
        name = game or tr("点 + 从正在运行的程序里选择")
        if not game:
            p.setFont(theme.font(10))
        p.drawText(QRectF(x, pad + 20, self.W - x - pad, 26), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, int(self.W - x - pad)))
        if game:
            p.setPen(timer if kind == "recording" else faded(text, 150))
            p.setFont(QFont("Segoe UI", 19, QFont.DemiBold))
            p.drawText(QRectF(x, pad + 46, self.W - x - pad, 34), Qt.AlignVCenter | Qt.AlignLeft,
                       fmt_clock(seconds) if kind == "recording" else "--:--:--")
            today, total = self.game_totals(game)
            p.setPen(faded(text, 165))
            p.setFont(theme.font(9))
            p.drawText(QRectF(pad, self.H - 26, self.W - 2 * pad, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       tr("今天 {t}", t=fmt_dur(today)) + "   ·   " + tr("累计 {t}", t=fmt_dur(total)))
        else:
            p.setPen(faded(text, 150))
            p.setFont(theme.font(9))
            p.drawText(QRectF(x, pad + 50, self.W - x - pad, 40), Qt.AlignLeft | Qt.TextWordWrap,
                       tr("PlayClock 认不出的游戏，选一次之后就会自动计时。"))

    def paint_bar(self, p: QPainter, r: QRectF, kind: str, seconds: float, text: QColor):
        """Slim bar: status light + timer (or a short status when nothing is being recorded)."""
        p.setPen(QPen(faded(text, 120), 1))
        p.setBrush(QColor(self.STATUS_DOT[kind]))
        p.drawEllipse(QRectF(r.left() + 13, r.center().y() - 4, 8, 8))
        right = r.right() - (32 if kind == "none" else 14)
        box = QRectF(r.left() + 29, r.top(), right - r.left() - 29, r.height())
        if kind == "recording":
            p.setPen(text)
            p.setFont(QFont("Segoe UI", 12.5, QFont.DemiBold))
            p.drawText(box, Qt.AlignVCenter | Qt.AlignLeft, fmt_clock(seconds))
        else:
            short = {"paused": tr("已暂停"), "background": tr("不在前台"), "none": tr("未识别游戏")}[kind]
            p.setPen(faded(text, 190))
            p.setFont(theme.font(9.5))
            p.drawText(box, Qt.AlignVCenter | Qt.AlignLeft, p.fontMetrics().elidedText(short, Qt.ElideRight, int(box.width())))

    # --- moving ----------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.drag = None
            self.ctrl.set_cfg("overlay_pos", [self.x(), self.y()])

    def mouseDoubleClickEvent(self, _):
        if self.mode == "bar":
            self.set_mode("card")   # bar -> card; card -> main window
        else:
            self.ctrl.show_window()

    def pick_game(self):
        self.ctrl.add_running_pc_game(self)
        self.update()
