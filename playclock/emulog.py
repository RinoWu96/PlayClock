"""Read an emulator's own log for what its window title doesn't show.

Ryujinx 1.3+ keeps its window title at "Ryujinx <version>" while a game runs, but its log says which game
was loaded and when emulation is paused, resumed or stopped:

    00:00:04.385 |I| Loader Start: Application Loaded: <name> v1.0.0 [01008cf01baac000] [64-bit]
    00:07:41.486 |I| GUI.WindowThread Emulation Pause: Emulation was paused
    00:08:02.900 |I| GUI.WindowThread Emulation Resume: Emulation was resumed
    02:14:31.841 |W| HLE.GuestThread.90 KernelSvc : WaitProcessWideKeyAtomic() = TerminationRequested

Only the newest log file is followed, and only the bytes appended since the last poll are read.
"""
import os
import re
from pathlib import Path

LOADED = re.compile(r"Application Loaded: (?P<game>.+?)(?: v\d[\w.\-]*)? \[[0-9a-fA-F]{16}\]")
PAUSED = "Emulation was paused"
RESUMED = "Emulation was resumed"
ENDED = re.compile(r"= TerminationRequested|Stopping audio processor")


def ryujinx_log_dirs(exe_path: str) -> list[Path]:
    """Where Ryujinx writes logs: next to the exe (also portable mode) or in the user profile."""
    exe_dir = Path(exe_path).parent if exe_path else None
    dirs = []
    if exe_dir:
        dirs += [exe_dir / "Logs", exe_dir / "portable" / "Logs"]
    if os.environ.get("APPDATA"):
        dirs.append(Path(os.environ["APPDATA"]) / "Ryujinx" / "Logs")
    return [d for d in dirs if d.is_dir()]


class RyujinxLog:
    """Follows the newest Ryujinx log; `poll()` returns (game or None, paused)."""

    def __init__(self):
        self.file: Path | None = None
        self.offset = 0
        self.partial = b""
        self.game: str | None = None
        self.paused = False

    def _reset(self, file: Path | None):
        self.file, self.offset, self.partial, self.game, self.paused = file, 0, b"", None, False

    def feed(self, data: bytes) -> None:
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()  # an unfinished last line waits for the rest
        for raw in lines:
            line = raw.decode("utf-8", "replace")
            m = LOADED.search(line)
            if m:
                self.game, self.paused = m.group("game").strip(), False
            elif PAUSED in line:
                self.paused = True
            elif RESUMED in line:
                self.paused = False
            elif self.game and ENDED.search(line):
                self.game, self.paused = None, False

    def poll(self, exe_path: str) -> tuple[str | None, bool]:
        logs = [f for d in ryujinx_log_dirs(exe_path) for f in d.glob("*.log")]
        if not logs:
            self._reset(None)
            return None, False
        newest = max(logs, key=lambda f: f.stat().st_mtime)
        if newest != self.file:
            self._reset(newest)  # a new log = Ryujinx was restarted
        size = newest.stat().st_size
        if size < self.offset:
            self._reset(newest)
        if size > self.offset:
            with open(newest, "rb") as f:
                f.seek(self.offset)
                self.feed(f.read(size - self.offset))
            self.offset = size
        return self.game, self.paused
