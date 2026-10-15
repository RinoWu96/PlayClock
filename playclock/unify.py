"""One name per game: merge the names a game was recorded under when its emulator / launcher showed it in
another language (Simplified, Traditional, English…), using stable ids — the Switch title id (Ryujinx log,
Ryujinx's games/<title id> folders, Switch emulator window titles) and the Steam appid — then redo those
imports so a renamed import isn't counted on top of the merged one."""
from pathlib import Path

from .db import DB, bad_name
from .emulog import log_titles
from .importers import RYUJINX, apply_import, plan_import, ryujinx_titles, scan_ryujinx


def unify_switch(db: DB, log_dirs: list[Path], roots: list[Path]) -> None:
    seen = log_titles(log_dirs)
    current: dict[str, str] = {}
    for root in roots:
        current.update(ryujinx_titles(root))
    for key in set(seen) | set(current):
        names = [current.get(key), *reversed(seen.get(key, []))]  # what Ryujinx shows now comes first
        names = [n for n in dict.fromkeys(names) if n]
        # readable names first; a garbled one can still be named through an alias the user made
        for n in [n for n in names if not bad_name(n)] + [n for n in names if bad_name(n)]:
            db.canonical(n, key)
    if roots and db.con.execute("SELECT 1 FROM sessions WHERE source=? LIMIT 1", (RYUJINX,)).fetchone():
        apply_import(db, plan_import(db, scan_ryujinx(roots[0])))


def unify_steam(db: DB, played: dict[int, str], variants: dict[int, list[str]]) -> None:
    """`played`: appid -> the name the current import uses; `variants`: appid -> all of Steam's names for it.
    Only names already recorded as Steam games are merged, so another platform's game is never swallowed."""
    steam_games = {r[0] for r in db.con.execute("SELECT DISTINCT game FROM sessions WHERE emulator='Steam'")}
    for appid, current in played.items():
        key = f"steam:{appid}"
        names = [current, *variants.get(appid, [])]
        for n in dict.fromkeys(n for n in names if n and (n == current or n in steam_games)):
            db.canonical(n, key)


def unify(db: DB, log_dirs: list[Path], roots: list[Path], steam=None) -> int:
    """Both of the above; `steam` = (played, variants) when Steam sync is on. Returns how many names merged."""
    before = len(db.games())
    unify_switch(db, log_dirs, roots)
    if steam:
        unify_steam(db, *steam)
    return before - len(db.games())
