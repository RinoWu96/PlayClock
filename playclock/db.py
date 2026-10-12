"""SQLite storage. Each session stores its own counted seconds (sleep/idle gaps are not counted).

Sync fields: every session has a global `uid`, the `device` that recorded it and an `updated` wall-clock
time (last writer wins); deletions leave a tombstone so other devices don't bring the row back.
"""
import csv
import hashlib
import sqlite3
import time
import uuid
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
CREATE TABLE IF NOT EXISTS steam_names (
    appid INTEGER PRIMARY KEY,
    name  TEXT NOT NULL     -- looked up online for apps missing from Steam's local cache
);
CREATE TABLE IF NOT EXISTS aliases (
    raw     TEXT PRIMARY KEY,  -- name parsed from the window title
    name    TEXT NOT NULL,     -- name it is recorded as
    updated REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tombstones (
    uid TEXT PRIMARY KEY,      -- a deleted session, so syncing never revives it
    at  REAL NOT NULL
);
"""

# Imported totals (Steam, Ryujinx) have no real dates: they count in totals and rankings ("all time")
# but not in anything per day/week/month. Tracked and hand-entered time is dated.
DATED = "(source IS NULL OR source='manual')"

RENAMED_EMULATORS = {"yuzu 系": "yuzu & forks", "Citra 系 (3DS)": "Citra & forks"}
RENAMED_GAMES = {"RetroArch（未识别游戏）": "RetroArch (unrecognized game)"}


def new_uid() -> str:
    return uuid.uuid4().hex


class DB:
    def __init__(self, path: Path, device: str = "local"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.con = sqlite3.connect(str(path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self._migrate()
        with self.con:
            for old, new in RENAMED_EMULATORS.items():
                self.con.execute("UPDATE sessions SET emulator=? WHERE emulator=?", (new, old))
            for old, new in RENAMED_GAMES.items():
                self.con.execute("UPDATE sessions SET game=? WHERE game=?", (new, old))

    def _migrate(self):
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(sessions)")}
        with self.con:
            if "source" not in cols:  # v0.1 had no imported sessions
                self.con.execute("ALTER TABLE sessions ADD COLUMN source TEXT")
            if "uid" not in cols:  # before sync existed
                for col in ("uid TEXT", "device TEXT", "updated REAL NOT NULL DEFAULT 0"):
                    self.con.execute(f"ALTER TABLE sessions ADD COLUMN {col}")
                for (sid,) in self.con.execute("SELECT id FROM sessions").fetchall():
                    self.con.execute("UPDATE sessions SET uid=?, device=?, updated=end WHERE id=?",
                                     (new_uid(), self.device, sid))
            self.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid)")
            if "updated" not in {r[1] for r in self.con.execute("PRAGMA table_info(aliases)")}:
                self.con.execute("ALTER TABLE aliases ADD COLUMN updated REAL NOT NULL DEFAULT 0")

    def _tombstone(self, where: str, args: tuple) -> None:
        """Delete sessions matching `where`, remembering their uids (call inside a transaction)."""
        now = time.time()
        uids = [(r[0], now) for r in self.con.execute(f"SELECT uid FROM sessions WHERE {where}", args)]
        self.con.executemany("INSERT OR REPLACE INTO tombstones(uid, at) VALUES(?, ?)", uids)
        self.con.execute(f"DELETE FROM sessions WHERE {where}", args)

    # --- names -------------------------------------------------------------
    def resolve(self, raw: str) -> str:
        row = self.con.execute("SELECT name FROM aliases WHERE raw=?", (raw,)).fetchone()
        return row["name"] if row else raw

    def rename_game(self, old: str, new: str) -> None:
        """Rename/merge: existing sessions move to `new`, and future detections of `old` too."""
        now = time.time()
        with self.con:
            self.con.execute("UPDATE sessions SET game=?, updated=? WHERE game=?", (new, now, old))
            self.con.execute("UPDATE aliases SET name=?, updated=? WHERE name=?", (new, now, old))
            self.con.execute("INSERT OR REPLACE INTO aliases(raw, name, updated) VALUES(?, ?, ?)", (old, new, now))
            self.con.execute("DELETE FROM aliases WHERE raw=name")
            # keep the old cover if the target has none
            self.con.execute("INSERT OR IGNORE INTO meta(game, cover) SELECT ?, cover FROM meta WHERE game=?", (new, old))
            self.con.execute("UPDATE meta SET cover=(SELECT cover FROM meta WHERE game=?) WHERE game=? AND cover IS NULL",
                             (old, new))
            self.con.execute("DELETE FROM meta WHERE game=?", (old,))

    def delete_game(self, game: str) -> None:
        with self.con:
            self._tombstone("game=?", (game,))
            self.con.execute("DELETE FROM meta WHERE game=?", (game,))

    def get_cover(self, game: str) -> str | None:
        row = self.con.execute("SELECT cover FROM meta WHERE game=?", (game,)).fetchone()
        return row["cover"] if row else None

    def set_cover(self, game: str, cover: str | None) -> None:
        with self.con:
            self.con.execute("INSERT INTO meta(game, cover) VALUES(?, ?) ON CONFLICT(game) DO UPDATE SET cover=excluded.cover",
                             (game, cover))

    # --- sessions ----------------------------------------------------------
    def start_session(self, game: str, emulator: str, now: float) -> int:
        with self.con:
            cur = self.con.execute(
                "INSERT INTO sessions(game, emulator, start, end, seconds, uid, device, updated) "
                "VALUES(?,?,?,?,0,?,?,?)",
                (game, emulator, now, now, new_uid(), self.device, time.time()))
        return cur.lastrowid

    def recent_session(self, game: str, since: float) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM sessions WHERE game=? AND end>=? AND source IS NULL ORDER BY end DESC LIMIT 1",
            (game, since)).fetchone()

    def update_session(self, sid: int, end: float, seconds: float) -> None:
        with self.con:
            self.con.execute("UPDATE sessions SET end=?, seconds=?, updated=? WHERE id=?",
                             (end, seconds, time.time(), sid))

    def get_session(self, sid: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def delete_session(self, sid: int) -> None:
        with self.con:
            self._tombstone("id=?", (sid,))

    def drop_short(self, min_seconds: float) -> None:
        """Remove tracked sessions too short to keep (leftovers of a crash); imported ones stay."""
        with self.con:
            self._tombstone("seconds<? AND source IS NULL AND device=?", (min_seconds, self.device))

    # --- imported history ----------------------------------------------------
    def tracked_total(self, game: str, emulator: str) -> float:
        """Time PlayClock tracked itself on this device for a game on one platform/emulator
        (an emulator's own records on this device can only overlap with this device's tracking)."""
        return self.con.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM sessions "
            "WHERE game=? AND emulator=? AND source IS NULL AND device=?",
            (game, emulator, self.device)).fetchone()[0]

    def steam_names(self) -> dict[int, str]:
        return {r[0]: r[1] for r in self.con.execute("SELECT appid, name FROM steam_names")}

    def add_steam_name(self, appid: int, name: str) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO steam_names(appid, name) VALUES(?, ?)", (appid, name))

    def add_manual(self, game: str, platform: str, seconds: float, end: float) -> None:
        """Time entered by hand (e.g. played on a console)."""
        with self.con:
            self.con.execute(
                "INSERT INTO sessions(game, emulator, start, end, seconds, source, uid, device, updated) "
                "VALUES(?,?,?,?,?,'manual',?,?,?)",
                (game, platform, end - seconds, end, seconds, new_uid(), self.device, time.time()))

    def imported_total(self, game: str, source: str) -> float:
        return self.con.execute("SELECT COALESCE(SUM(seconds), 0) FROM sessions WHERE game=? AND source=?",
                                (game, source)).fetchone()[0]

    def replace_import(self, game: str, emulator: str, source: str, seconds: float, end: float | None) -> None:
        """One imported session per (game, source); re-importing replaces it.

        Its uid is derived from (source, game) so devices importing the same thing agree on it. Account-wide
        sources ("steam:<account>") are the same everywhere; others (Ryujinx) are per device."""
        scope = "" if source.startswith("steam:") else self.device
        uid = hashlib.sha1(f"{scope}|{source}|{game}".encode()).hexdigest()
        with self.con:
            self._tombstone("game=? AND source=? AND uid<>?", (game, source, uid))
            if seconds > 0:
                end = end or time.time()
                self.con.execute(
                    "INSERT INTO sessions(game, emulator, start, end, seconds, source, uid, device, updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET game=excluded.game, "
                    "emulator=excluded.emulator, start=excluded.start, end=excluded.end, "
                    "seconds=excluded.seconds, updated=excluded.updated",
                    (game, emulator, end - seconds, end, seconds, source, uid, self.device, time.time()))
                self.con.execute("DELETE FROM tombstones WHERE uid=?", (uid,))
            else:
                self._tombstone("uid=?", (uid,))

    # --- queries -----------------------------------------------------------
    def game_stats(self, since: float = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            f"""SELECT game,
                      GROUP_CONCAT(DISTINCT emulator) AS emulators,
                      SUM(seconds) AS total, COUNT(*) AS n, MAX(end) AS last
               FROM sessions {"" if not since else "WHERE start>=? AND " + DATED}
               GROUP BY game ORDER BY total DESC""",
            (since,) if since else ()).fetchall()

    def game_detail(self, game: str) -> sqlite3.Row | None:
        return self.con.execute(
            f"""SELECT game, GROUP_CONCAT(DISTINCT emulator) AS emulators, SUM(seconds) AS total,
                      COUNT(*) AS n, MIN(CASE WHEN {DATED} THEN start END) AS first, MAX(end) AS last
               FROM sessions WHERE game=?""", (game,)).fetchone()

    def total_since(self, since: float) -> float:
        return self.con.execute(f"SELECT COALESCE(SUM(seconds), 0) FROM sessions WHERE start>=? AND {DATED}",
                                (since,)).fetchone()[0]

    def played_days(self, since: float = 0) -> int:
        return self.con.execute(
            f"SELECT COUNT(DISTINCT date(start, 'unixepoch', 'localtime')) FROM sessions WHERE start>=? AND {DATED}",
            (since,)).fetchone()[0]

    def has_imports(self) -> bool:
        return self.con.execute(f"SELECT 1 FROM sessions WHERE NOT {DATED} LIMIT 1").fetchone() is not None

    def first_dated(self) -> float | None:
        return self.con.execute(f"SELECT MIN(start) FROM sessions WHERE {DATED}").fetchone()[0]

    def emulator_stats(self, since: float = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            f"""SELECT emulator, SUM(seconds) AS total FROM sessions {"" if not since else "WHERE start>=? AND " + DATED}
               GROUP BY emulator ORDER BY total DESC""", (since,) if since else ()).fetchall()

    def monthly(self, months: int = 12) -> list[tuple[str, float]]:
        """(YYYY-MM, seconds) for the last `months` months, oldest first, zero-filled."""
        got = {r[0]: r[1] for r in self.con.execute(
            "SELECT strftime('%Y-%m', start, 'unixepoch', 'localtime') AS m, SUM(seconds) FROM sessions "
            f"WHERE {DATED} GROUP BY m")}
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

    def emulators(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT DISTINCT emulator FROM sessions")]

    def games(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT DISTINCT game FROM sessions ORDER BY game")]

    def daily(self, days: int = 30, game: str | None = None) -> list[tuple[str, float]]:
        """(YYYY-MM-DD, seconds) for the last `days` local days, oldest first, zero-filled."""
        lt = time.localtime()
        since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - days + 1, 0, 0, 0, 0, 0, -1))
        q = """SELECT date(start, 'unixepoch', 'localtime') AS d, SUM(seconds) AS s
               FROM sessions WHERE start>=? AND """ + DATED + (" AND game=?" if game else "") + " GROUP BY d"
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
            w.writerow([tr("游戏"), tr("平台"), tr("开始"), tr("结束"), tr("时长(分钟)")])
            for r in rows:
                w.writerow([r["game"], r["emulator"],
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["start"])),
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["end"])),
                            round(r["seconds"] / 60, 1)])
        return len(rows)
