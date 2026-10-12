@echo off
cd /d %~dp0
.venv\Scripts\python.exe make_icon.py || exit /b 1
.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name PlayClock --icon icon.ico ^
  --distpath dist --workpath build ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtMultimedia --exclude-module PySide6.Qt3DCore ^
  main.py
