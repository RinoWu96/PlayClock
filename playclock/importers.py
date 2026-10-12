"""Import playtime history that emulators recorded themselves (currently Ryujinx).

Import is idempotent and never double counts: for each game the imported amount is
"emulator's total" minus "what PlayClock tracked itself", stored as one session tagged with `source`;
re-importing replaces that session.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import DB

RYUJINX = "ryujinx"


@dataclass
class EmuGame:
    title: str
    seconds: float
    last_played: float | None  # epoch seconds


@dataclass
class PlanRow:
    game: str           # name as PlayClock records it (aliases applied)
    emulator_total: float
    tracked: float      # PlayClock's own tracking for this game
    add: float          # what the import will store
    last_played: float | None


def parse_timespan(s: str) -> float:
    """.NET TimeSpan text: [d.]hh:mm:ss[.fffffff]"""
    m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)(?:\.\d+)?", (s or "").strip())
    if not m:
        return 0.0
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return float(((d * 24 + h) * 60 + mi) * 60 + sec)


def parse_utc(s: str | None) -> float | None:
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", s)
    if not m:
        return None
    return datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp()


def ryujinx_roots() -> list[Path]:
    """Ryujinx data folders that exist (standard install location)."""
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Ryujinx")
    return [r for r in roots if (r / "games").is_dir()]


def scan_ryujinx(root: Path) -> list[EmuGame]:
    """Games with recorded playtime under a Ryujinx data folder (or its portable folder)."""
    games_dir = root / "games" if (root / "games").is_dir() else root / "portable" / "games"
    out = []
    for meta in sorted(games_dir.glob("*/gui/metadata.json")):
        try:
            d = json.loads(meta.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        secs = parse_timespan(d.get("timespan_played", ""))
        title = (d.get("title") or "").strip()
        if secs > 0 and title:
            out.append(EmuGame(title, secs, parse_utc(d.get("last_played_utc"))))
    return out


def plan_import(db: DB, games: list[EmuGame]) -> list[PlanRow]:
    rows = []
    for g in games:
        name = db.resolve(g.title)
        tracked = db.tracked_total(name)
        rows.append(PlanRow(name, g.seconds, tracked, max(0.0, g.seconds - tracked), g.last_played))
    return rows


def pending_seconds(db: DB, plan: list[PlanRow], source: str = RYUJINX) -> float:
    """Time the emulator has that PlayClock doesn't (neither tracked nor imported yet)."""
    return sum(max(0.0, r.add - db.imported_total(r.game, source)) for r in plan)


def apply_import(db: DB, plan: list[PlanRow], emulator: str = "Ryujinx", source: str = RYUJINX) -> int:
    n = 0
    for r in plan:
        db.replace_import(r.game, emulator, source, r.add, r.last_played)
        n += r.add > 0
    return n
