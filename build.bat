@echo off
cd /d %~dp0
.venv\Scripts\python.exe make_icon.py || exit /b 1
.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name PlayClock --icon icon.ico ^
  --distpath dist --workpath build --add-data "playclock\assets;playclock\assets" ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtMultimedia --exclude-module PySide6.Qt3DCore ^
  main.py
copy /y THIRD_PARTY_NOTICES.md dist\PlayClock\ >nul || exit /b 1
rem Qt parts PlayClock never uses that PySide6's hooks still copy: QML/Quick, PDF (its image plugin loads
rem Qt6Pdf.dll into the running app) and the 20 MB software OpenGL renderer (only Quick/OpenGL apps need it).
set QT=dist\PlayClock\_internal\PySide6
for %%f in (plugins\imageformats\qpdf.dll Qt6Pdf.dll Qt6Qml.dll Qt6QmlMeta.dll Qt6QmlModels.dll ^
            Qt6QmlWorkerScript.dll Qt6Quick.dll opengl32sw.dll) do if exist "%QT%\%%f" del /q "%QT%\%%f"
