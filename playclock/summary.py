"""summary.json: a small, pre-computed view of the merged records for read-only viewers (the WeChat
mini program). Viewers can only GET one known file (no WebDAV directory listing, no gzip), so this is
plain JSON with everything a phone needs, written next to the device files on each sync.

Day-based figures are given as a daily list so the viewer computes "today / this week / this month"
against its own clock — the file may have been written yesterday.
"""
import json
import time

from .db import DATED, DB

FORMAT = 1
NAME = "summary.json"
ACTIVE_WINDOW = 240   # a tracked session updated within this many seconds counts as "playing now"
DAYS = 60
RECENT = 30


def build_summary(db: DB, platform_of, device_names: dict[str, str], now: float | None = None) -> dict:
    now = time.time() if now is None else now

    def platforms(emulators: str | None) -> list[str]:
        out = []
        for e in (emulators or "").split(","):
            p = platform_of(e)
            if e and p not in out:
                out.append(p)
        return out

    playing = [{
        "game": r["game"], "platform": platform_of(r["emulator"]), "start": r["start"], "seconds": r["seconds"],
        "updated": r["end"], "device": device_names.get(r["device"], r["device"]),
    } for r in db.con.execute(
        "SELECT game, emulator, start, seconds, end, device FROM sessions "
        "WHERE source IS NULL AND end >= ? ORDER BY end DESC", (now - ACTIVE_WINDOW,))]

    per_platform: dict[str, float] = {}
    for r in db.emulator_stats():
        p = platform_of(r["emulator"])
        per_platform[p] = per_platform.get(p, 0.0) + r["total"]

    games = [{"name": r["game"], "platforms": platforms(r["emulators"]), "total": r["total"], "sessions": r["n"],
              "last": r["last"]} for r in db.game_stats()]

    recent = [{"game": r["game"], "platform": platform_of(r["emulator"]), "start": r["start"], "end": r["end"],
               "seconds": r["seconds"]} for r in db.con.execute(
        f"SELECT game, emulator, start, end, seconds FROM sessions WHERE {DATED} ORDER BY end DESC LIMIT ?",
        (RECENT,))]

    return {
        "format": FORMAT,
        "generated": now,
        "playing": playing,
        "total": sum(g["total"] for g in games),
        "daily": [[d, s] for d, s in db.daily(DAYS)],
        "games": games,
        "platforms": sorted(([p, s] for p, s in per_platform.items()), key=lambda kv: -kv[1]),
        "recent": recent,
        "has_imports": db.has_imports(),
    }


def content_hash(summary: dict) -> str:
    """Hash without the timestamp, so an unchanged summary isn't re-uploaded."""
    import hashlib
    body = {k: v for k, v in summary.items() if k != "generated"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def encode_summary(summary: dict) -> bytes:
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
