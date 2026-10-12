"""SQLite storage. Each session stores its own counted seconds (sleep/idle gaps are not counted)."""
import csv
import sqlite3
import time
from pathlib import Path

from .i18n import tr

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    game     TEXT NOT NULL,
    emulator TEXT NOT NULL,
    start    REAL NOT NULL,
    end      REAL NOT NULL,
    seconds  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_game ON sessions(game);
CREATE INDEX IF NOT EXISTS idx_sessions_end ON sessions(end);
CREATE TABLE IF NOT EXISTS meta (
    game  TEXT PRIMARY KEY,
    cover TEXT              -- file name inside data/covers
);
CREATE TABLE IF NOT EXISTS aliases (
    raw  TEXT PRIMARY KEY,  -- name parsed from the window title
    name TEXT NOT NULL      -- name it is recorded as
);
"""

RENAMED_EMULATORS = {"yuzu 系": "yuzu & forks", "Citra 系 (3DS)": "Citra & forks"}
RENAMED_GAMES = {"RetroArch（未识别游戏）": "RetroArch (unrecognized game)"}


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(sessions)")}
        if "source" not in cols:  # v0.1 had no imported sessions
            self.con.execute("ALTER TABLE sessions ADD COLUMN source TEXT")
        with self.con:
            for old, new in RENAMED_EMULATORS.items():
                self.con.execute("UPDATE sessions SET emulator=? WHERE emulator=?", (new, old))
            for old, new in RENAMED_GAMES.items():
                self.con.execute("UPDATE sessions SET game=? WHERE game=?", (new, old))

    # --- names -------------------------------------------------------------
    def resolve(self, raw: str) -> str:
        row = self.con.execute("SELECT name FROM aliases WHERE raw=?", (raw,)).fetchone()
        return row["name"] if row else raw

    def rename_game(self, old: str, new: str) -> None:
        """Rename/merge: existing sessions move to `new`, and future detections of `old` too."""
        with self.con:
            self.con.execute("UPDATE sessions SET game=? WHERE game=?", (new, old))
            self.con.execute("UPDATE aliases SET name=? WHERE name=?", (new, old))
            self.con.execute("INSERT OR REPLACE INTO aliases(raw, name) VALUES(?, ?)", (old, new))
            self.con.execute("DELETE FROM aliases WHERE raw=name")
            # keep the old cover if the target has none
            self.con.execute("INSERT OR IGNORE INTO meta(game, cover) SELECT ?, cover FROM meta WHERE game=?", (new, old))
            self.con.execute("UPDATE meta SET cover=(SELECT cover FROM meta WHERE game=?) WHERE game=? AND cover IS NULL",
                             (old, new))
            self.con.execute("DELETE FROM meta WHERE game=?", (old,))

    def delete_game(self, game: str) -> None:
        with self.con:
            self.con.execute("DELETE FROM sessions WHERE game=?", (game,))
            self.con.execute("DELETE FROM meta WHERE game=?", (game,))

    def get_cover(self, game: str) -> str | None:
        row = self.con.execute("SELECT cover FROM meta WHERE game=?", (game,)).fetchone()
        return row["cover"] if row else None

    def covers(self) -> dict[str, str]:
        return {r["game"]: r["cover"] for r in self.con.execute("SELECT * FROM meta WHERE cover IS NOT NULL")}

    def set_cover(self, game: str, cover: str | None) -> None:
        with self.con:
            self.con.execute("INSERT INTO meta(game, cover) VALUES(?, ?) ON CONFLICT(game) DO UPDATE SET cover=excluded.cover",
                             (game, cover))

    # --- sessions ----------------------------------------------------------
    def start_session(self, game: str, emulator: str, now: float) -> int:
        with self.con:
            cur = self.con.execute(
                "INSERT INTO sessions(game, emulator, start, end, seconds) VALUES(?,?,?,?,0)",
                (game, emulator, now, now))
        return cur.lastrowid

    def recent_session(self, game: str, since: float) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM sessions WHERE game=? AND end>=? AND source IS NULL ORDER BY end DESC LIMIT 1",
            (game, since)).fetchone()

    def update_session(self, sid: int, end: float, seconds: float) -> None:
        with self.con:
            self.con.execute("UPDATE sessions SET end=?, seconds=? WHERE id=?", (end, seconds, sid))

    def get_session(self, sid: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def delete_session(self, sid: int) -> None:
        with self.con:
            self.con.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def drop_short(self, min_seconds: float, keep_id: int | None = None) -> None:
        with self.con:
            self.con.execute("DELETE FROM sessions WHERE seconds<? AND id IS NOT ? AND source IS NULL",
                             (min_seconds, keep_id))

    # --- imported history ----------------------------------------------------
    def tracked_total(self, game: str) -> float:
        return self.con.execute("SELECT COALESCE(SUM(seconds), 0) FROM sessions WHERE game=? AND source IS NULL",
                                (game,)).fetchone()[0]

    def imported_total(self, game: str, source: str) -> float:
        return self.con.execute("SELECT COALESCE(SUM(seconds), 0) FROM sessions WHERE game=? AND source=?",
                                (game, source)).fetchone()[0]

    def replace_import(self, game: str, emulator: str, source: str, seconds: float, end: float | None) -> None:
        """One imported session per (game, source); re-importing replaces it."""
        with self.con:
            self.con.execute("DELETE FROM sessions WHERE game=? AND source=?", (game, source))
            if seconds > 0:
                end = end or time.time()
                self.con.execute(
                    "INSERT INTO sessions(game, emulator, start, end, seconds, source) VALUES(?,?,?,?,?,?)",
                    (game, emulator, end - seconds, end, seconds, source))

    # --- queries -----------------------------------------------------------
    def game_stats(self, since: float = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            """SELECT game,
                      GROUP_CONCAT(DISTINCT emulator) AS emulators,
                      SUM(seconds) AS total, COUNT(*) AS n, MAX(end) AS last
               FROM sessions WHERE start>=? GROUP BY game ORDER BY total DESC""",
            (since,)).fetchall()

    def game_detail(self, game: str) -> sqlite3.Row | None:
        return self.con.execute(
            """SELECT game, GROUP_CONCAT(DISTINCT emulator) AS emulators, SUM(seconds) AS total,
                      COUNT(*) AS n, MIN(start) AS first, MAX(end) AS last
               FROM sessions WHERE game=?""", (game,)).fetchone()

    def total_since(self, since: float) -> float:
        return self.con.execute("SELECT COALESCE(SUM(seconds), 0) FROM sessions WHERE start>=?", (since,)).fetchone()[0]

    def emulator_stats(self, since: float = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            """SELECT emulator, SUM(seconds) AS total FROM sessions WHERE start>=?
               GROUP BY emulator ORDER BY total DESC""", (since,)).fetchall()

    def monthly(self, months: int = 12) -> list[tuple[str, float]]:
        """(YYYY-MM, seconds) for the last `months` months, oldest first, zero-filled."""
        got = {r[0]: r[1] for r in self.con.execute(
            "SELECT strftime('%Y-%m', start, 'unixepoch', 'localtime') AS m, SUM(seconds) FROM sessions GROUP BY m")}
        lt = time.localtime()
        y, m = lt.tm_year, lt.tm_mon
        out = []
        for _ in range(months):
            key = f"{y:04d}-{m:02d}"
            out.append((key, got.get(key, 0.0)))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        return out[::-1]

    def sessions(self, game: str | None = None, limit: int = 2000) -> list[sqlite3.Row]:
        if game:
            q, a = "SELECT * FROM sessions WHERE game=? ORDER BY start DESC LIMIT ?", (game, limit)
        else:
            q, a = "SELECT * FROM sessions ORDER BY start DESC LIMIT ?", (limit,)
        return self.con.execute(q, a).fetchall()

    def games(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT DISTINCT game FROM sessions ORDER BY game")]

    def daily(self, days: int = 30, game: str | None = None) -> list[tuple[str, float]]:
        """(YYYY-MM-DD, seconds) for the last `days` local days, oldest first, zero-filled."""
        lt = time.localtime()
        since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - days + 1, 0, 0, 0, 0, 0, -1))
        q = """SELECT date(start, 'unixepoch', 'localtime') AS d, SUM(seconds) AS s
               FROM sessions WHERE start>=?""" + (" AND game=?" if game else "") + " GROUP BY d"
        got = {r["d"]: r["s"] for r in self.con.execute(q, (since, game) if game else (since,))}
        out = []
        for i in range(days - 1, -1, -1):
            d = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
            out.append((d, got.get(d, 0.0)))
        return out

    def export_csv(self, path: Path) -> int:
        rows = self.con.execute("SELECT * FROM sessions ORDER BY start").fetchall()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([tr("游戏"), tr("模拟器"), tr("开始"), tr("结束"), tr("时长(分钟)")])
            for r in rows:
                w.writerow([r["game"], r["emulator"],
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["start"])),
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["end"])),
                            round(r["seconds"] / 60, 1)])
        return len(rows)
