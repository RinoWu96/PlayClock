# Third-party software

PlayClock's own source code is published without an open-source license: all rights reserved.
The Windows build bundles the following third-party software, each under its own license.

## Qt for Python (PySide6 6.8.1) and Qt 6.8

- License: GNU Lesser General Public License v3 (LGPL-3.0); see https://www.gnu.org/licenses/lgpl-3.0.html
- Source code: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://download.qt.io/official_releases/qt/
- PySide6 and the Qt libraries are shipped unmodified as separate DLL / .pyd files in the `_internal` folder,
  so they can be replaced with other compatible builds.

## Python 3.11

- License: Python Software Foundation License; see https://docs.python.org/3/license.html

## PyInstaller 6.11.1 (bootloader)

- License: GPL-2.0 with the PyInstaller bootloader exception, which allows distributing the bundled
  program under any terms; see https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt
