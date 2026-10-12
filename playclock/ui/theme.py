"""Colours, fonts and the application stylesheet."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0e1014"
SIDEBAR = "#13161c"
CARD = "#191d25"
CARD_HOVER = "#212632"
BORDER = "#262b36"
TEXT = "#eceef2"
MUTED = "#8b93a3"
FAINT = "#5c6475"
ACCENT = "#6d8dff"
ACCENT_2 = "#a46dff"
GREEN = "#3ddc97"
DANGER = "#ff6b6b"

FONT_FAMILY = "Microsoft YaHei UI"

# Series colours for rankings (distinct, readable on dark).
SERIES = ["#6d8dff", "#a46dff", "#3ddc97", "#ffb547", "#ff6b9a", "#4cc9f0", "#c3e66b", "#ff8c5a", "#9aa5ff", "#e08cff"]


def font(size: float = 10, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-family: "{FONT_FAMILY}"; font-size: 10pt; }}
QMainWindow, #content {{ background: {BG}; }}
#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#brand {{ font-size: 15pt; font-weight: 700; }}
#brandSub {{ color: {MUTED}; font-size: 9pt; }}

QPushButton#nav {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 10px;
    color: {MUTED}; font-size: 10.5pt; background: transparent;
}}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD_HOVER}; color: {TEXT}; font-weight: 600; }}

#pageTitle {{ font-size: 20pt; font-weight: 700; }}
#pageSub {{ color: {MUTED}; }}
#sectionTitle {{ font-size: 12.5pt; font-weight: 600; }}
#muted {{ color: {MUTED}; }}
#faint {{ color: {FAINT}; font-size: 9pt; }}

#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#tileValue {{ font-size: 19pt; font-weight: 700; }}
#tileLabel {{ color: {MUTED}; font-size: 9.5pt; }}
#tileSub {{ color: {FAINT}; font-size: 9pt; }}

QPushButton {{
    background: {CARD_HOVER}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 16px; color: {TEXT};
}}
QPushButton:hover {{ background: #2a3040; border-color: #343b4a; }}
QPushButton:pressed {{ background: #1c212b; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #8099ff; }}
QPushButton#danger {{ background: transparent; border: 1px solid #5a2c33; color: {DANGER}; }}
QPushButton#danger:hover {{ background: #2a1619; }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 8px; padding: 6px 14px; color: {MUTED}; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {CARD_HOVER}; color: {TEXT}; font-weight: 600; }}
#segBar {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}

QLineEdit, QComboBox, QSpinBox, QDateEdit {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {CARD_HOVER}; padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}


QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3240; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4254; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2c3240; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTableWidget {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px;
    gridline-color: transparent; alternate-background-color: #1c2029;
    selection-background-color: {CARD_HOVER}; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 10px; border: none; }}
QHeaderView::section {{
    background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 10px; font-weight: 600;
}}
QHeaderView {{ background: transparent; }}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARD_HOVER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolTip {{ background: #232835; color: {TEXT}; border: 1px solid #343b4a; border-radius: 6px; padding: 6px 8px; }}
QMessageBox, QInputDialog, QFileDialog {{ background: {CARD}; }}
QTextEdit {{ background: #12151b; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, CARD),
        (QPalette.AlternateBase, "#1c2029"), (QPalette.Text, TEXT), (QPalette.Button, CARD_HOVER),
        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, "#232835"), (QPalette.ToolTipText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, FAINT),
    ]:
        p.setColor(role, QColor(c))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    app.setPalette(p)
    app.setFont(font(10))
    app.setStyleSheet(STYLESHEET)
