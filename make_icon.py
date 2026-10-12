"""Write icon.ico for the exe (run before PyInstaller)."""
from PySide6.QtWidgets import QApplication

from playclock.ui.widgets import app_icon

app = QApplication([])
app_icon(size=256).save("icon.ico")
