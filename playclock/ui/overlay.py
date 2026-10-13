"""Floating panel: a small always-on-top window showing what is being recorded right now.

It never takes keyboard focus (so clicking it doesn't leave the game), is themed from the current game's
cover, and only exists while shown — hiding it destroys it, so it costs nothing in the background.
"""
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QWidget

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


class Overlay(QWidget):
    W, H = 380, 124

    def __init__(self, ctrl):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.ctrl = ctrl
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("PlayClock")
        self.setFixedSize(self.W, self.H)
        self.drag: QPoint | None = None
        self.totals: tuple[str, float, float, float] | None = None  # (game, today, all time, when computed)

        self.buttons = {}
        for key, text, tip, slot in (
                ("add", "+", tr("选择正在运行的游戏"), self.pick_game),
                ("open", "⌂", tr("打开主界面"), lambda: ctrl.show_window()),
                ("hide", "×", tr("隐藏浮窗（Ctrl+Alt+P）"), lambda: ctrl.set_overlay(False))):
            b = QPushButton(text, self)
            b.setToolTip(tip)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            b.setStyleSheet("QPushButton { background: rgba(255,255,255,0.10); border: none; border-radius: 12px;"
                            " color: rgba(255,255,255,0.85); font-size: 13pt; padding: 0 0 2px 0; }"
                            "QPushButton:hover { background: rgba(255,255,255,0.25); color: white; }")
            self.buttons[key] = b
        for i, key in enumerate(("hide", "open", "add")):
            self.buttons[key].move(self.W - 34 - i * 28, 10)

        pos = ctrl.cfg.get("overlay_pos")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if pos and any(s.availableGeometry().contains(QPoint(*pos)) for s in QGuiApplication.screens()):
            self.move(*pos)
        else:
            self.move(screen.right() - self.W - 24, screen.top() + 24)

        self.clock = QTimer(self)  # repaint once a second while shown
        self.clock.timeout.connect(self.update)
        self.clock.start(1000)

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
    def paintEvent(self, _):
        kind, game, emulator, seconds = self.state()
        self.buttons["add"].setVisible(kind == "none")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pixmap = self.ctrl.cover(game) if game else None
        accent = accent_of(game, pixmap) if game else QColor(theme.ACCENT)

        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        base = QColor(accent).darker(420)
        base.setAlpha(240)
        end = QColor("#0e1014")
        end.setAlpha(240)
        g.setColorAt(0, base)
        g.setColorAt(1, end)
        p.fillPath(path, g)
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 140), 1.2))
        p.drawPath(path)

        pad = 14
        if game:
            cover = QRectF(pad, pad, 128, 72)
            paint_cover(p, cover, game, pixmap, 10)
            x = cover.right() + 14
        else:
            x = pad + 4
        dot, status = {
            "recording": (QColor(theme.GREEN), tr("记录中") + (f" · {emulator}" if emulator else "")),
            "paused": (QColor("#ffb547"), tr("游戏已暂停，不计时")),
            "background": (QColor(theme.MUTED), tr("游戏不在前台，暂停计时")),
            "none": (QColor(theme.FAINT), tr("未识别到游戏")),
        }[kind]
        p.setPen(Qt.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QRectF(x, pad + 5, 8, 8))
        p.setPen(QColor(255, 255, 255, 190))
        p.setFont(theme.font(9))
        p.drawText(QRectF(x + 14, pad - 1, self.W - x - 110, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(status, Qt.ElideRight, int(self.W - x - 110)))
        p.setPen(QColor("white"))
        p.setFont(theme.font(11.5, QFont.Bold))
        name = game or tr("点 + 从正在运行的程序里选择")
        if not game:
            p.setFont(theme.font(10))
        p.drawText(QRectF(x, pad + 20, self.W - x - pad, 26), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, int(self.W - x - pad)))
        if game:
            p.setPen(accent.lighter(130) if kind == "recording" else QColor(255, 255, 255, 150))
            p.setFont(QFont("Segoe UI", 19, QFont.DemiBold))
            p.drawText(QRectF(x, pad + 46, self.W - x - pad, 34), Qt.AlignVCenter | Qt.AlignLeft,
                       fmt_clock(seconds) if kind == "recording" else "--:--:--")
            today, total = self.game_totals(game)
            p.setPen(QColor(255, 255, 255, 150))
            p.setFont(theme.font(9))
            p.drawText(QRectF(pad, self.H - 26, self.W - 2 * pad, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       tr("今天 {t}", t=fmt_dur(today)) + "   ·   " + tr("累计 {t}", t=fmt_dur(total)))
        else:
            p.setPen(QColor(255, 255, 255, 140))
            p.setFont(theme.font(9))
            p.drawText(QRectF(x, pad + 50, self.W - x - pad, 40), Qt.AlignLeft | Qt.TextWordWrap,
                       tr("PlayClock 认不出的游戏，选一次之后就会自动计时。"))

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
        self.ctrl.show_window()

    def pick_game(self):
        self.ctrl.add_running_pc_game(self)
        self.update()
