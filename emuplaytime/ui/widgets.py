"""Reusable painted widgets: covers, game cards, charts, tiles, toggle, flow layout, icons."""
import math
import time
import zlib
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLayout, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from . import theme
from ..i18n import fmt_ago, fmt_dur, tr  # noqa: F401  (re-exported)

# --------------------------------------------------------------------------- formatting


def fmt_clock(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fmt_date(ts: float, with_time: bool = True) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d", time.localtime(ts))


# --------------------------------------------------------------------------- covers

_pixmaps: dict[str, QPixmap] = {}


def cover_pixmap(path: Path | None) -> QPixmap | None:
    if not path:
        return None
    key = str(path)
    if key not in _pixmaps:
        pm = QPixmap(key)
        _pixmaps[key] = pm if not pm.isNull() else None
    return _pixmaps[key]


def hue_of(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % 360


def initials(name: str) -> str:
    name = name.strip()
    if not name:
        return "?"
    if any("぀" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in name[:2]):
        return name[:2]
    words = [w for w in name.replace(":", " ").replace("-", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or name[:2].upper()


def paint_cover(p: QPainter, rect: QRectF, game: str, pixmap: QPixmap | None, radius: float,
                badge: str | None = None):
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    if pixmap:
        scaled = pixmap.scaled(rect.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        sx = (scaled.width() - rect.width()) / 2
        sy = (scaled.height() - rect.height()) / 2
        p.drawPixmap(rect.toRect(), scaled, QRect(int(sx), int(sy), int(rect.width()), int(rect.height())))
    else:
        h = hue_of(game)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, QColor.fromHsl(h, 150, 110))
        g.setColorAt(1, QColor.fromHsl((h + 50) % 360, 160, 46))
        p.fillRect(rect, g)
        p.setPen(Qt.NoPen)
        d = rect.height() * 1.1
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawEllipse(QRectF(rect.right() - d * 0.62, rect.top() - d * 0.35, d, d))
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(QRectF(rect.left() - d * 0.3, rect.bottom() - d * 0.45, d * 0.8, d * 0.8))
        f = theme.font(max(12.0, rect.height() * 0.24), QFont.Bold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(rect, Qt.AlignCenter, initials(game))
    if badge:
        f = theme.font(8, QFont.DemiBold)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(badge) + 14
        r = QRectF(rect.left() + 8, rect.bottom() - 26, w, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(r, Qt.AlignCenter, badge)
    p.restore()


class Cover(QWidget):
    def __init__(self, w: int, h: int, radius: float = 12):
        super().__init__()
        self.setFixedSize(w, h)
        self.radius = radius
        self.game = ""
        self.pixmap = None
        self.badge = None

    def set(self, game: str, pixmap: QPixmap | None, badge: str | None = None):
        self.game, self.pixmap, self.badge = game, pixmap, badge
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_cover(p, QRectF(self.rect()), self.game, self.pixmap, self.radius, self.badge)


# --------------------------------------------------------------------------- basic blocks


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class Card(QFrame):
    def __init__(self, padding: int = 20, spacing: int = 12):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(spacing)


class StatTile(Card):
    def __init__(self, title: str, accent: str = theme.ACCENT):
        super().__init__(padding=18, spacing=4)
        self.accent = accent
        self.title = label(title, "tileLabel")
        self.value = label("—", "tileValue")
        self.sub = label("", "tileSub")
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.value)
        self.lay.addWidget(self.sub)

    def set(self, value: str, sub: str = ""):
        self.value.setText(value)
        self.sub.setText(sub)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.accent))
        p.drawRoundedRect(QRectF(18, self.height() - 4, 28, 3), 1.5, 1.5)


class Toggle(QAbstractButton):
    """iOS-style switch with a text label on its right."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(46 + 12 + self.fontMetrics().horizontalAdvance(self.text()), 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0, (self.height() - 24) / 2, 44, 24)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if self.isChecked() else "#2e3443"))
        p.drawRoundedRect(track, 12, 12)
        x = track.right() - 21 if self.isChecked() else track.left() + 3
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, track.top() + 3, 18, 18))
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(56, 0, self.width() - 56, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class SegmentBar(QFrame):
    """Row of mutually exclusive buttons; emits the chosen key."""
    changed = Signal(object)

    def __init__(self, options: list[tuple[str, object]], current=None):
        super().__init__()
        from PySide6.QtWidgets import QButtonGroup, QPushButton
        self.setObjectName("segBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.keys = []
        for i, (text, key) in enumerate(options):
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == current if current is not None else i == 0)
            self.group.addButton(b, i)
            self.keys.append(key)
            lay.addWidget(b)
        self.group.idClicked.connect(lambda i: self.changed.emit(self.keys[i]))

    def current(self):
        return self.keys[self.group.checkedId()]


# --------------------------------------------------------------------------- game card


class GameCard(QFrame):
    clicked = Signal(str)
    menu_requested = Signal(str, QPoint)

    W, H = 232, 131

    def __init__(self, game: str, emulator: str, total: float, last: float, pixmap: QPixmap | None,
                 playing: bool = False):
        super().__init__()
        self.game = game
        self.playing = playing
        self.hover = False
        self.setFixedSize(self.W, self.H + 74)
        self.setCursor(Qt.PointingHandCursor)
        self.pixmap = pixmap
        self.emulator = emulator.split(",")[0]
        self.total = total
        self.last = last
        self.setToolTip(game)

    def enterEvent(self, _):
        self.hover = True
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.game)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.game, e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.ACCENT if self.hover else theme.BORDER), 1))
        p.setBrush(QColor(theme.CARD_HOVER if self.hover else theme.CARD))
        p.drawRoundedRect(bg, 14, 14)
        cover = QRectF(6, 6, self.W - 12, self.H - 6)
        paint_cover(p, cover, self.game, self.pixmap, 10, self.emulator)
        if self.playing:
            r = QRectF(cover.right() - 74, cover.top() + 8, 66, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.GREEN))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor("#062a1b"))
            p.setFont(theme.font(8, QFont.Bold))
            p.drawText(r, Qt.AlignCenter, tr("● 游玩中"))
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(10.5, QFont.DemiBold))
        name = p.fontMetrics().elidedText(self.game, Qt.ElideRight, self.W - 28)
        p.drawText(QRectF(14, self.H + 10, self.W - 28, 24), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setFont(theme.font(9))
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(14, self.H + 36, self.W - 28, 22), Qt.AlignLeft | Qt.AlignVCenter, fmt_dur(self.total))
        p.setPen(QColor(theme.FAINT))
        p.drawText(QRectF(14, self.H + 36, self.W - 28, 22), Qt.AlignRight | Qt.AlignVCenter, fmt_ago(self.last))


# --------------------------------------------------------------------------- now playing hero


class NowPlaying(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(190)
        self.game = None
        self.emulator = ""
        self.seconds = 0.0
        self.total = 0.0
        self.pixmap = None

    def set(self, game, emulator="", seconds=0.0, total=0.0, pixmap=None):
        self.game, self.emulator, self.seconds, self.total, self.pixmap = game, emulator, seconds, total, pixmap
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setClipPath(path)
        if self.game:
            h = hue_of(self.game)
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor.fromHsl(h, 120, 52))
            g.setColorAt(0.55, QColor.fromHsl((h + 30) % 360, 90, 32))
            g.setColorAt(1, QColor("#151821"))
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#232a3d"))
            g.setColorAt(1, QColor("#161a22"))
        p.fillRect(r, g)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 10))
        p.drawEllipse(QRectF(r.right() - 260, r.top() - 140, 380, 380))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 18, 18)

        pad = 26
        cover_h = r.height() - 2 * pad
        cover = QRectF(pad, pad, cover_h * 16 / 9, cover_h)
        if self.game:
            paint_cover(p, cover, self.game, self.pixmap, 12)
            x = cover.right() + 28
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.GREEN))
            p.drawEllipse(QPointF(x + 5, pad + 14), 5, 5)
            p.setPen(QColor(255, 255, 255, 200))
            p.setFont(theme.font(9.5, QFont.DemiBold))
            p.drawText(QPointF(x + 18, pad + 19), tr("正在游玩 · {emu}", emu=self.emulator))
            p.setPen(QColor("white"))
            p.setFont(theme.font(20, QFont.Bold))
            name = p.fontMetrics().elidedText(self.game, Qt.ElideRight, int(r.right() - x - 260))
            p.drawText(QRectF(x, pad + 30, r.right() - x - 240, 44), Qt.AlignLeft | Qt.AlignVCenter, name)
            p.setPen(QColor(255, 255, 255, 170))
            p.setFont(theme.font(10))
            p.drawText(QRectF(x, pad + 78, 400, 24), Qt.AlignLeft | Qt.AlignVCenter, tr("累计 {t}", t=fmt_dur(self.total)))
            # session clock on the right
            p.setPen(QColor(255, 255, 255, 160))
            p.setFont(theme.font(9.5))
            p.drawText(QRectF(r.right() - 240, pad + 6, 214, 22), Qt.AlignRight | Qt.AlignVCenter, tr("本次游玩"))
            p.setPen(QColor("white"))
            p.setFont(QFont("Segoe UI", 30, QFont.DemiBold))
            p.drawText(QRectF(r.right() - 300, pad + 30, 274, 56), Qt.AlignRight | Qt.AlignVCenter,
                       fmt_clock(self.seconds))
        else:
            p.setPen(QColor(theme.TEXT))
            p.setFont(theme.font(17, QFont.Bold))
            p.drawText(QRectF(pad + 4, pad + 18, r.width() - 2 * pad, 40), Qt.AlignLeft | Qt.AlignVCenter,
                       tr("现在没有在玩游戏"))
            p.setPen(QColor(theme.MUTED))
            p.setFont(theme.font(10))
            p.drawText(QRectF(pad + 4, pad + 64, r.width() - 2 * pad, 60), Qt.AlignLeft | Qt.TextWordWrap,
                       tr("在模拟器里启动游戏后会自动开始计时，无需任何操作。\n关闭窗口后程序会留在系统托盘继续记录。"))


# --------------------------------------------------------------------------- charts


def nice_step(peak_hours: float) -> float:
    for s in (0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 24, 48, 100, 200, 500):
        if peak_hours / s <= 5:
            return s
    return 1000


class BarChart(QWidget):
    """Vertical bars of seconds; data = [(axis label, tooltip label, seconds)]."""

    def __init__(self, height: int = 220, color: str = theme.ACCENT):
        super().__init__()
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.data: list[tuple[str, str, float]] = []
        self.color = QColor(color)
        self.hover = -1

    def set_data(self, data):
        self.data = data
        self.hover = -1
        self.update()

    def _geom(self):
        left, right, top, bottom = 46, 8, 12, 30
        n = max(len(self.data), 1)
        w = (self.width() - left - right) / n
        h = self.height() - top - bottom
        peak = max([s for *_, s in self.data] + [0]) / 3600
        step = nice_step(max(peak, 0.5))
        top_val = max(step * math.ceil(peak / step), step)
        return left, top, w, h, step, top_val

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, top, w, h, step, top_val = self._geom()
        p.setFont(theme.font(8.5))
        v = 0.0
        while v <= top_val + 1e-9:
            y = top + h - h * v / top_val
            p.setPen(QPen(QColor(255, 255, 255, 14), 1, Qt.DashLine if v else Qt.SolidLine))
            p.drawLine(QPointF(left, y), QPointF(self.width() - 8, y))
            p.setPen(QColor(theme.FAINT))
            txt = f"{v:g}h"
            p.drawText(QRectF(0, y - 9, left - 8, 18), Qt.AlignRight | Qt.AlignVCenter, txt)
            v += step
        n = len(self.data)
        every = max(1, math.ceil(n / 8))
        bw = min(w * 0.62, 34)
        for i, (axis, _tip, s) in enumerate(self.data):
            x = left + i * w + (w - bw) / 2
            bh = h * (s / 3600) / top_val
            if s > 0:
                c = QColor(self.color)
                if self.hover not in (-1, i):
                    c.setAlpha(110)
                g = QLinearGradient(QPointF(0, top + h - bh), QPointF(0, top + h))
                g.setColorAt(0, c.lighter(115))
                g.setColorAt(1, c.darker(125))
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                path = QPainterPath()
                rr = min(5, bw / 2, bh / 2)
                path.addRoundedRect(QRectF(x, top + h - bh, bw, bh), rr, rr)
                p.drawPath(path)
            if (n - 1 - i) % every == 0:
                p.setPen(QColor(theme.TEXT if i == self.hover else theme.FAINT))
                p.drawText(QRectF(left + i * w - 30 + w / 2, top + h + 6, 60, 20), Qt.AlignCenter, axis)
        if not any(s for *_, s in self.data):
            p.setPen(QColor(theme.FAINT))
            p.setFont(theme.font(10))
            p.drawText(QRectF(left, top, self.width() - left, h), Qt.AlignCenter, tr("这段时间还没有记录"))

    def mouseMoveEvent(self, e):
        if not self.data:
            return
        left, top, w, h, *_ = self._geom()
        i = int((e.position().x() - left) // w) if w else -1
        if not 0 <= i < len(self.data):
            i = -1
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            _, tip, s = self.data[i]
            QToolTip.showText(e.globalPosition().toPoint(), f"{tip}\n{fmt_dur(s)}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()


class RankBars(QWidget):
    """Horizontal ranking bars; rows = [(name, seconds)]."""
    clicked = Signal(str)

    ROW = 38

    def __init__(self, colors: list[str] | None = None, clickable: bool = False):
        super().__init__()
        self.rows: list[tuple[str, float]] = []
        self.colors = colors or theme.SERIES
        self.clickable = clickable
        self.hover = -1
        self.setMouseTracking(True)

    def set_rows(self, rows):
        self.rows = rows
        self.setFixedHeight(max(1, len(rows)) * self.ROW + 4)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            p.setPen(QColor(theme.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, tr("暂无数据"))
            return
        total = sum(s for _, s in self.rows) or 1
        peak = max(s for _, s in self.rows) or 1
        name_w = min(220, self.width() * 0.34)
        val_w = 130
        bar_w = self.width() - name_w - val_w - 24
        for i, (name, s) in enumerate(self.rows):
            y = i * self.ROW + 2
            c = QColor(self.colors[i % len(self.colors)])
            if self.hover == i and self.clickable:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 8))
                p.drawRoundedRect(QRectF(0, y, self.width(), self.ROW - 4), 8, 8)
            p.setFont(theme.font(9.5))
            p.setPen(QColor(theme.TEXT))
            shown = p.fontMetrics().elidedText(name, Qt.ElideRight, int(name_w - 12))
            p.drawText(QRectF(8, y, name_w - 8, self.ROW - 4), Qt.AlignVCenter | Qt.AlignLeft, shown)
            track = QRectF(name_w + 8, y + (self.ROW - 4) / 2 - 5, bar_w, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(track, 5, 5)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(track.left(), track.top(), max(10, bar_w * s / peak), 10), 5, 5)
            p.setPen(QColor(theme.MUTED))
            p.setFont(theme.font(9))
            p.drawText(QRectF(self.width() - val_w, y, val_w - 4, self.ROW - 4), Qt.AlignVCenter | Qt.AlignRight,
                       f"{fmt_dur(s, short=True)}  ·  {s / total:.0%}")

    def _row_at(self, y):
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        i = self._row_at(e.position().y())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if self.clickable and i >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._row_at(e.position().y())
        if self.clickable and i >= 0:
            self.clicked.emit(self.rows[i][0])


# --------------------------------------------------------------------------- flow layout


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 16):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x = rect.x()
                y += line + sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        return y + line - rect.y()

    def clear(self):
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().deleteLater()


# --------------------------------------------------------------------------- icons


def _icon_path(kind: str) -> QPainterPath:
    p = QPainterPath()
    if kind == "home":
        p.moveTo(3, 10); p.lineTo(12, 3); p.lineTo(21, 10); p.lineTo(21, 21); p.lineTo(14.5, 21)
        p.lineTo(14.5, 14.5); p.lineTo(9.5, 14.5); p.lineTo(9.5, 21); p.lineTo(3, 21); p.closeSubpath()
    elif kind == "library":
        for x, y in ((3, 3), (13, 3), (3, 13), (13, 13)):
            p.addRoundedRect(QRectF(x, y, 8, 8), 2, 2)
    elif kind == "history":
        p.addEllipse(QPointF(12, 12), 9, 9)
        p.moveTo(12, 7); p.lineTo(12, 12); p.lineTo(15.5, 14)
    elif kind == "stats":
        p.addRoundedRect(QRectF(3, 12, 4.5, 9), 1.5, 1.5)
        p.addRoundedRect(QRectF(9.75, 6, 4.5, 15), 1.5, 1.5)
        p.addRoundedRect(QRectF(16.5, 3, 4.5, 18), 1.5, 1.5)
    elif kind == "settings":
        p.addEllipse(QPointF(12, 12), 3, 3)
        for k in range(8):
            a = k * math.pi / 4
            p.moveTo(12 + 6 * math.cos(a), 12 + 6 * math.sin(a))
            p.lineTo(12 + 9.5 * math.cos(a), 12 + 9.5 * math.sin(a))
        p.addEllipse(QPointF(12, 12), 6, 6)
    elif kind == "back":
        p.moveTo(15, 5); p.lineTo(8, 12); p.lineTo(15, 19)
    elif kind == "search":
        p.addEllipse(QPointF(10.5, 10.5), 6.5, 6.5)
        p.moveTo(15.5, 15.5); p.lineTo(21, 21)
    return p


def icon(kind: str, color: str = theme.MUTED, active: str = theme.TEXT, size: int = 20) -> QIcon:
    ic = QIcon()
    for c, state in ((color, QIcon.Off), (active, QIcon.On)):
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size * 2 / 24, size * 2 / 24)
        p.setPen(QPen(QColor(c), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_icon_path(kind))
        p.end()
        pm.setDevicePixelRatio(2)
        ic.addPixmap(pm, QIcon.Normal, state)
    return ic


def app_icon(active: bool = False, size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)
    g = QLinearGradient(0, 0, 64, 64)
    if active:
        g.setColorAt(0, QColor("#3ddc97"))
        g.setColorAt(1, QColor("#1f9e8a"))
    else:
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor(theme.ACCENT_2))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 15, 15)
    # gamepad silhouette
    p.setBrush(QColor(255, 255, 255, 245))
    body = QPainterPath()
    body.addRoundedRect(QRectF(10, 20, 44, 26), 13, 13)
    p.drawPath(body)
    c = QColor("#3a3f86") if not active else QColor("#13624f")
    p.setPen(QPen(c, 3.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(18, 33), QPointF(26, 33))
    p.drawLine(QPointF(22, 29), QPointF(22, 37))
    # clock in place of the buttons
    p.setPen(QPen(c, 2.4))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(41.5, 33), 6.5, 6.5)
    p.setPen(QPen(c, 2.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(41.5, 33), QPointF(41.5, 29.2))
    p.drawLine(QPointF(41.5, 33), QPointF(44.3, 34.6))
    p.end()
    return pm
