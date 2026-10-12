"""Polling tracker: detect which game is running and accumulate time into the DB."""
import time
from dataclasses import dataclass

from .db import DB
from .rules import Rule
from .winapi import Window, list_windows


@dataclass
class Detection:
    emulator: str
    game: str
    title: str
    foreground: bool


@dataclass
class Current:
    sid: int
    game: str
    emulator: str
    seconds: float
    end: float = 0.0       # last time counted (in memory; written on flush)
    saved: float = 0.0     # when the row was last written


def detect(rules: list[Rule], windows: list[Window]) -> tuple[list[Detection], list[tuple[str, Window]]]:
    """Return (games found, all emulator windows seen) — the second list is for the debug view."""
    seen: list[tuple[str, Window]] = []
    found: list[Detection] = []
    by_pid: dict[int, list[Window]] = {}
    for w in windows:
        by_pid.setdefault(w.pid, []).append(w)
    for pid_windows in by_pid.values():
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
                    hit = Detection(rule.name, game, w.title, w.foreground)
        if hit is None and rule.fallback and any(not w.minimized for w in pid_windows):
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

        self.detections, self.seen = detect(self.rules, list_windows() if windows is None else windows)
        d = self._pick(self.detections)
        game = self.db.resolve(d.game) if d else None

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
