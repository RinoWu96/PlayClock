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
THUMB_DIR = "covers"          # PlayClock/covers/<cover file stem>.jpg on the WebDAV side
THUMB_SIZE = (460, 215)       # Steam header size: matches the cards on both desktop and phone
THUMB_BATCH = 60              # thumbnails uploaded per sync, so the first upload doesn't block one long sync
ACTIVE_WINDOW = 240   # a tracked session updated within this many seconds counts as "playing now"
DAYS = 60
RECENT = 30


def thumb_name(cover_file: str) -> str:
    """Remote thumbnail name for a local cover file (data/covers/<uuid>.png -> <uuid>.jpg)."""
    return cover_file.rsplit(".", 1)[0] + ".jpg"


def make_thumb(path) -> bytes | None:
    """Centre-cropped 460x215 JPEG of a cover image. Safe on a worker thread (QImage, not QPixmap)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        return None
    w, h = THUMB_SIZE
    img = img.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    img = img.copy((img.width() - w) // 2, (img.height() - h) // 2, w, h).convertToFormat(QImage.Format_RGB32)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", 82)
    return bytes(data)


def build_summary(db: DB, platform_of, device_names: dict[str, str], now: float | None = None,
                  covers: dict[str, str] | None = None) -> dict:
    """`covers`: game -> local cover file name; games with one get "cover": "covers/<stem>.jpg"."""
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

    covers = covers or {}
    # "emulators" = how each game was played, for the library badge: Ryujinx, Steam, or the platform of a
    # hand-entered session (Switch, PC)
    games = [{"name": r["game"], "platforms": platforms(r["emulators"]),
              "emulators": [e for e in (r["emulators"] or "").split(",") if e], "total": r["total"], "sessions": r["n"],
              "last": r["last"],
              "cover": f"{THUMB_DIR}/{thumb_name(covers[r['game']])}" if covers.get(r["game"]) else None}
             for r in db.game_stats()]

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
        "platforms": sorted(([p, s] for p, s in per_platform.items()), key=lambda kv: -kv[1]),        "recent": recent,
        "has_imports": db.has_imports(),
    }


def content_hash(summary: dict) -> str:
    """Hash without the timestamp, so an unchanged summary isn't re-uploaded."""
    import hashlib
    body = {k: v for k, v in summary.items() if k != "generated"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def encode_summary(summary: dict) -> bytes:
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
