"""Polling tracker: detect which game is running and accumulate time into the DB."""
import re
import time
from dataclasses import dataclass

from .db import DB
from .emulog import RyujinxLog
from .pc import PC, lookup, norm
from .rules import Rule
from .winapi import Window, list_windows


@dataclass
class Detection:
    emulator: str
    game: str
    title: str
    foreground: bool
    key: str | None = None  # stable game id when known ("switch:<title id>")


TITLE_ID = re.compile(r"[(\[]([0-9a-fA-F]{16})[)\]]")  # "(0100F2C0115B6000)" in Switch emulator titles


@dataclass
class Current:
    sid: int
    game: str
    emulator: str
    seconds: float
    end: float = 0.0       # last time counted (in memory; written on flush)
    saved: float = 0.0     # when the row was last written


def detect(rules: list[Rule], windows: list[Window], pc_games: dict[str, str] | None = None,
           logs: dict[str, RyujinxLog] | None = None) -> tuple[list[Detection], list[tuple[str, Window]]]:
    """Return (games found, all emulator / PC game windows seen) — the second list is for the debug view.
    `pc_games`: normalised exe path -> game name (pc.lookup), checked before the emulator rules.
    `logs`: per-exe log readers kept between polls, for emulators whose title shows no game."""
    seen: list[tuple[str, Window]] = []
    found: list[Detection] = []
    by_pid: dict[int, list[Window]] = {}
    for w in windows:
        by_pid.setdefault(w.pid, []).append(w)
    for pid_windows in by_pid.values():
        path = pid_windows[0].path
        if pc_games and path and norm(path) in pc_games:
            seen += [(PC, w) for w in pid_windows]
            shown = [w for w in pid_windows if not w.minimized]
            if shown:
                w = max(shown, key=lambda w: w.area)
                found.append(Detection(PC, pc_games[norm(path)], w.title, w.foreground))
            continue
        rule = next((r for r in rules if r.matches_exe(pid_windows[0].exe)), None)
        if rule is None:
            continue
        hit = None
        # Biggest window first: the game window wins over settings dialogs.
        for w in sorted(pid_windows, key=lambda w: w.area, reverse=True):
            seen.append((rule.name, w))
            if hit is None and not w.minimized:
                game = rule.game_from_title(w.title)
                if game:
                    tid = TITLE_ID.search(w.title) if rule.platform == "Switch" else None
                    hit = Detection(rule.name, game, w.title, w.foreground,
                                    f"switch:{tid.group(1).lower()}" if tid else None)
        shown = [w for w in pid_windows if not w.minimized]
        if hit is None and rule.log == "ryujinx" and shown and logs is not None and pid_windows[0].path:
            reader = logs.setdefault(pid_windows[0].path, RyujinxLog())
            game, paused = reader.poll(pid_windows[0].path)
            if game and not paused:  # a paused game isn't being played
                w = max(shown, key=lambda w: w.area)
                hit = Detection(rule.name, game, w.title, w.foreground, reader.key)
        if hit is None and rule.fallback and shown:
            w = max(pid_windows, key=lambda w: w.area)
            hit = Detection(rule.name, rule.fallback, w.title, w.foreground)
        if hit:
            found.append(hit)
    return found, seen


class Tracker:
    FLUSH_EVERY = 30  # seconds between DB writes while a game runs (a crash loses at most this much)

    def __init__(self, db: DB, rules: list[Rule], cfg: dict):
        self.db = db
        self.rules = rules
        self.cfg = cfg
        self.current: Current | None = None
        self.last_tick: float | None = None
        self.seen: list[tuple[str, Window]] = []
        self.detections: list[Detection] = []
        self.logs: dict[str, RyujinxLog] = {}  # emulator log readers, kept so each poll reads only new lines

    def _pick(self, found: list[Detection]) -> Detection | None:
        if self.cfg.get("foreground_only"):
            found = [d for d in found if d.foreground]
        if not found:
            return None
        # Prefer the foreground one, then the one already being tracked.
        for d in found:
            if d.foreground:
                return d
        if self.current:
            for d in found:
                if self.db.resolve(d.game) == self.current.game:
                    return d
        return found[0]

    def tick(self, now: float | None = None, windows: list[Window] | None = None) -> Current | None:
        now = time.time() if now is None else now
        interval = float(self.cfg.get("interval", 5))
        dt = 0.0 if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        # A long gap means the PC slept or the app was blocked: don't count it.
        continuous = 0 < dt <= max(3 * interval, 30)

        self.detections, self.seen = detect(self.rules, list_windows() if windows is None else windows,
                                            lookup(self.cfg.get("pc_games")), self.logs)
        d = self._pick(self.detections)
        game = self.db.canonical(d.game, d.key) if d else None  # one name per game id, garbled names dropped
        if game is None:
            d = None

        if self.current and game == self.current.game and continuous:
            self.current.seconds += dt
            self.current.end = now
            if now - self.current.saved >= self.FLUSH_EVERY:
                self.flush()
            return self.current

        self.stop()
        if d:
            resume_gap = float(self.cfg.get("resume_gap", 180))
            prev = self.db.recent_session(game, now - resume_gap)
            if prev:
                self.current = Current(prev["id"], game, prev["emulator"], prev["seconds"], now)
                self.flush()
            else:
                sid = self.db.start_session(game, d.emulator, now)
                self.current = Current(sid, game, d.emulator, 0.0, now, now)
        return self.current

    def flush(self) -> None:
        """Write the running session to the DB."""
        c = self.current
        if c:
            self.db.update_session(c.sid, c.end, c.seconds)
            c.saved = c.end

    def stop(self) -> None:
        """End the current session; drop it if it is shorter than min_session."""
        if self.current is None:
            return
        if self.current.seconds < float(self.cfg.get("min_session", 60)):
            self.db.delete_session(self.current.sid)
        else:
            self.flush()
        self.current = None
