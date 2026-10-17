#!/usr/bin/env python3
"""PlayClock for Steam Deck / SteamOS (and other Linux): records emulator play time in the background and
syncs it to the user's own WebDAV storage (坚果云 by default) in PlayClock's device-file format, so the
Windows app shows the Deck as one of its devices. Standard library only; no UI.

    python3 playclock_deck.py setup     # WebDAV account + app password (asked in the terminal)
    python3 playclock_deck.py run       # the background loop (started by the systemd user service)
    python3 playclock_deck.py status    # what is detected right now, sessions, last sync
    python3 playclock_deck.py sync      # upload now
    python3 playclock_deck.py doctor    # diagnostics: emulator processes, Ryujinx paths, Python

How a game is found: every INTERVAL seconds, /proc is scanned for the user's emulator processes. EmuDeck
starts every game from Steam (Steam ROM Manager adds them as non-Steam shortcuts), so the emulator process
carries SteamGameId: its shortcut in shortcuts.vdf gives the name shown in the Steam library. Without that,
the game is the ROM on the emulator's command line, or for Ryujinx the game in its own log (which also says
when emulation is paused). Suspend freezes everything, so a long wall-clock gap between polls is not counted.
Play time recorded before installing comes from Ryujinx's own totals and Steam's per-shortcut play time.
"""
from __future__ import annotations

import argparse
import calendar
import getpass
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

VERSION = "0.3.0"
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "playclock"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share") / "playclock"
JIANGUOYUN = "https://dav.jianguoyun.com/dav/"
FOLDER = "PlayClock"
FORMAT = 1

INTERVAL = 10       # seconds between polls
MIN_SESSION = 60    # shorter sessions are dropped
RESUME_GAP = 180    # reopening the same game within this continues the session
FLUSH_EVERY = 60    # seconds between state-file writes while playing
SYNC_EVERY = 600    # upload at least this often (and whenever a session ends)

# name (same as the Windows rules, so the PC maps it to a platform), process-name pattern, ROM extensions
EMULATORS = [
    ("Ryujinx", r"ryujinx|ryubing", {"nsp", "xci", "nca", "nro", "nsz", "xcz"}),
    ("yuzu & forks", r"yuzu|suyu|sudachi|citron|eden|torzu", {"nsp", "xci", "nca", "nro", "nsz", "xcz"}),
    ("Citra & forks", r"citra|lime3ds|azahar|mandarine", {"3ds", "cci", "cxi", "cia", "3dsx", "app", "z3ds"}),
    ("Dolphin", r"dolphin-emu|primehack", {"iso", "rvz", "gcm", "wbfs", "ciso", "gcz", "wad", "dol", "elf", "wia"}),
    ("PCSX2", r"pcsx2", {"iso", "chd", "cso", "zso", "bin", "gz", "elf", "m3u"}),
    ("DuckStation", r"duckstation", {"cue", "bin", "chd", "pbp", "iso", "m3u", "ecm", "mds", "img"}),
    ("PPSSPP", r"ppsspp", {"iso", "cso", "pbp", "chd", "elf", "prx"}),
    ("RPCS3", r"rpcs3", {"bin", "self", "elf"}),
    ("Cemu", r"cemu", {"wud", "wux", "wua", "rpx", "elf"}),
    ("Vita3K", r"vita3k", {"vpk"}),
    ("melonDS", r"melonds", {"nds", "dsi", "srl"}),
    ("mGBA", r"mgba", {"gba", "gbc", "gb", "sgb"}),
    ("Flycast", r"flycast", {"gdi", "cdi", "chd", "cue"}),
    ("shadPS4", r"shadps4", {"bin", "elf"}),
    ("xemu", r"xemu", {"iso", "xiso"}),
    ("RetroArch", r"retroarch", {"nes", "fds", "sfc", "smc", "n64", "z64", "v64", "md", "gen", "smd", "sms", "gg",
                                 "pce", "gba", "gbc", "gb", "nds", "iso", "cue", "chd", "bin", "zip", "7z", "a26",
                                 "lnx", "ws", "wsc", "ngp", "ngc", "32x", "psx", "pbp"}),
]
_EMU = [(name, re.compile(pattern, re.I), exts) for name, pattern, exts in EMULATORS]


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def load_savesync():
    """savesync.py (shared with the Windows app, playclock/savesync.py) is installed next to this file."""
    here = Path(__file__).resolve().parent
    for folder in (here, here.parent / "playclock"):
        path = folder / "savesync.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("playclock_savesync", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod  # dataclasses look their module up there
            spec.loader.exec_module(mod)
            return mod
    return None


savesync = load_savesync()
if savesync is None:
    sys.exit("savesync.py is missing next to playclock_deck.py: reinstall PlayClock for Steam Deck.")


# --------------------------------------------------------------------------- files


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data, private: bool = False) -> None:
    """Atomic write (temp file + rename); private files are readable by this user only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- detection


def clean_name(stem: str) -> str:
    """'Pokemon Platinum (USA) [b]' -> 'Pokemon Platinum'; title IDs, regions and dump tags go."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", " ", stem)
    name = re.sub(r"[_]+", " ", name)
    name = re.sub(r"\s+v?\d+(\.\d+)+\s*$", " ", name)  # trailing version
    return re.sub(r"\s+", " ", name).strip(" -.") or stem


def game_from_args(args: list[str], exts: set[str]) -> str | None:
    """The ROM named on an emulator's command line (last matching argument wins)."""
    for arg in reversed(args[1:]):
        if arg.startswith("-") and "=" not in arg:
            continue
        value = arg.split("=", 1)[1] if arg.startswith("-") else arg
        p = Path(value)
        ext = p.suffix.lower().lstrip(".")
        if ext in exts and ("/" in value or os.path.exists(value)):
            if p.name.upper() == "EBOOT.BIN" and len(p.parents) > 2:  # RPCS3: …/<GAME>/USRDIR/EBOOT.BIN
                return clean_name(p.parents[1].name)
            return clean_name(p.stem)
    return None


_procs: dict[int, tuple[float, tuple[str, list[str]] | None]] = {}  # pid -> (read at, (comm, argv) or None)
PROC_REFRESH = 60  # a known pid is read again after this long (a process can exec into another program)


def _read_proc(pid: int, uid: int) -> tuple[str, list[str]] | None:
    try:
        if os.stat(f"/proc/{pid}").st_uid != uid:
            return None
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return None
    args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    return (comm, args) if args else None


def processes():
    """(pid, comm, argv) for this user's processes (Linux /proc). Only new pids are read on each poll (and
    every pid again once a minute), which keeps the 10-second poll nearly free."""
    uid, now, live = os.getuid(), time.monotonic(), set()
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        live.add(pid)
        cached = _procs.get(pid)
        if cached is None or now - cached[0] > PROC_REFRESH:
            cached = _procs[pid] = (now, _read_proc(pid, uid))
        if cached[1]:
            yield (pid, *cached[1])
    for pid in [p for p in _procs if p not in live]:
        del _procs[pid]


def match_emulator(comm: str, args: list[str]):
    """The EMULATORS entry a process is, judged by its name and executable (not by its arguments)."""
    names = f"{comm} {os.path.basename(args[0])}"
    for entry in _EMU:
        if entry[1].search(names):
            return entry
    return None


LOADED = re.compile(r"Application Loaded: (?P<game>.+?)(?: v\d[\w.\-]*)? \[(?P<tid>[0-9a-fA-F]{16})\]")
ENDED = re.compile(r"= TerminationRequested|Stopping audio processor")
TITLE_ID = re.compile(r"(?<![0-9a-fA-F])(01[0-9a-fA-F]{14})(?![0-9a-fA-F])")  # Switch title id in a ROM name


def bad_name(name: str | None) -> bool:
    """Not a name: empty, Ryujinx's "<Unknown Name>", or bytes misread as text (same as the Windows app)."""
    name = (name or "").strip()
    return not name or name == "<Unknown Name>" or "\ufffd" in name or any(ord(ch) < 32 for ch in name)


def switch_key(text: str, emulator: str) -> str | None:
    """"switch:<title id>" when a Switch emulator's ROM path / options name a title id."""
    tid = TITLE_ID.search(text) if emulator in ("Ryujinx", "yuzu & forks") else None
    return f"switch:{tid.group(1).lower()}" if tid else None


def environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    pairs = (item.decode("utf-8", "replace").split("=", 1) for item in raw.split(b"\0") if b"=" in item)
    return {k: v for k, v in pairs}


def steam_appid(env: dict[str, str]) -> int | None:
    """The (32-bit) app id Steam started this process for: non-Steam shortcuts carry their 64-bit game id
    (appid << 32 | 0x02000000) in SteamGameId."""
    for var in ("SteamGameId", "SteamAppId"):
        try:
            n = int(env.get(var) or 0)
        except ValueError:
            continue
        if n < 0:  # a 64-bit id printed as a signed number
            n += 1 << 64
        if n:
            return n >> 32 if n > 0xFFFFFFFF else n
    return None


# --------------------------------------------------------------------------- Steam (EmuDeck's shortcuts)


def steam_roots() -> list[Path]:
    out = []
    for p in (HOME / ".local" / "share" / "Steam", HOME / ".steam" / "steam", HOME / ".steam" / "root"):
        if (p / "userdata").is_dir() and p.resolve() not in [o.resolve() for o in out]:
            out.append(p)
    return out


def read_binary_vdf(data: bytes) -> dict:
    """Steam's binary KeyValues as in shortcuts.vdf (NUL-terminated keys; keys lower-cased)."""
    def read(i):
        out = {}
        while i < len(data):
            t = data[i]
            i += 1
            if t == 0x08:
                return out, i
            j = data.index(b"\0", i)
            key = data[i:j].decode("utf-8", "replace").lower()
            i = j + 1
            if t == 0x00:
                out[key], i = read(i)
            elif t == 0x01:
                j = data.index(b"\0", i)
                out[key] = data[i:j].decode("utf-8", "replace")
                i = j + 1
            elif t == 0x02:
                out[key] = struct.unpack_from("<i", data, i)[0]
                i += 4
            elif t == 0x07:
                out[key] = struct.unpack_from("<Q", data, i)[0]
                i += 8
            else:
                raise ValueError(f"unknown KeyValues type {t:#x}")
        return out, i
    return read(0)[0]


def parse_vdf(text: str) -> dict:
    """Steam's text KeyValues (localconfig.vdf), keys lower-cased."""
    root: dict = {}
    stack, key = [root], None
    for s_, brace in re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        if brace == "{":
            new: dict = {}
            stack[-1][(key or "").lower()] = new
            stack.append(new)
            key = None
        elif brace == "}":
            stack.pop()
        elif key is None:
            key = s_
        else:
            stack[-1][key.lower()] = s_
            key = None
    return root


def shortcuts() -> dict[int, dict]:
    """Non-Steam shortcuts of every Steam account here: appid -> {name, exe, launch, last_played}."""
    out: dict[int, dict] = {}
    for root in steam_roots():
        for f in root.glob("userdata/*/config/shortcuts.vdf"):
            try:
                kv = read_binary_vdf(f.read_bytes()).get("shortcuts", {})
            except (OSError, ValueError, IndexError, struct.error):
                continue
            for v in kv.values():
                if isinstance(v, dict) and "appid" in v:
                    out[v["appid"] & 0xFFFFFFFF] = {
                        "name": (v.get("appname") or "").strip(), "exe": v.get("exe", ""),
                        "launch": v.get("launchoptions", ""), "last_played": v.get("lastplaytime") or None}
    return out


def shortcut_playtimes() -> dict[int, tuple[float, float | None]]:
    """Steam's own play time per app id from localconfig.vdf (minutes there; 64-bit game ids folded to app ids)."""
    out: dict[int, tuple[float, float | None]] = {}
    for root in steam_roots():
        for f in root.glob("userdata/*/config/localconfig.vdf"):
            try:
                kv = parse_vdf(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            apps = kv.get("userlocalconfigstore", {}).get("software", {}).get("valve", {}).get("steam", {}).get("apps", {})
            for k, d in apps.items():
                if not (k.isdigit() and isinstance(d, dict)):
                    continue
                n = int(k)
                appid = n >> 32 if n > 0xFFFFFFFF else n
                minutes = int(d.get("playtime", "0") or 0)
                if minutes > 0:
                    last = int(d.get("lastplayed", "0") or 0) or None
                    prev, prev_last = out.get(appid, (0.0, None))
                    out[appid] = (max(prev, minutes * 60.0), max(filter(None, (prev_last, last)), default=None))
    return out


def shortcut_emulator(sc: dict):
    """The EMULATORS entry a shortcut starts (EmuDeck launchers are named after the emulator)."""
    text = f"{sc.get('exe', '')} {sc.get('launch', '')}"
    return next((e for e in _EMU if e[1].search(text)), None)


def shortcut_totals() -> list[tuple[str, float, float | None, str | None, str]]:
    """Steam's play time for the emulator shortcuts: (name, seconds, last played, game id, emulator)."""
    scs, times = shortcuts(), shortcut_playtimes()
    out = []
    for appid, (secs, last) in times.items():
        sc = scs.get(appid)
        entry = shortcut_emulator(sc) if sc else None
        if not entry or bad_name(sc["name"]):
            continue  # not one of the emulator shortcuts
        key = switch_key(sc["launch"], entry[0]) or f"steamshortcut:{appid}"
        out.append((sc["name"], secs, last or sc.get("last_played"), key, entry[0]))
    return out


class RyujinxLog:
    """Follows the newest Ryujinx log (same logic as the Windows app's emulog.py)."""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self.file: Path | None = None
        self.offset = 0
        self.partial = b""
        self.game: str | None = None
        self.key: str | None = None
        self.paused = False

    def poll(self) -> tuple[str | None, bool]:
        logs = [f for d in self.dirs if d.is_dir() for f in d.glob("*.log")]
        if not logs:
            return None, False
        newest = max(logs, key=lambda f: f.stat().st_mtime)
        size = newest.stat().st_size
        if newest != self.file or size < self.offset:
            self.file, self.offset, self.partial, self.game, self.paused, self.key = newest, 0, b"", None, False, None
        if size > self.offset:
            with open(newest, "rb") as f:
                f.seek(self.offset)
                data = f.read(size - self.offset)
            self.offset = size
            lines = (self.partial + data).split(b"\n")
            self.partial = lines.pop()
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                m = LOADED.search(line)
                if m:
                    self.game, self.paused = m.group("game").strip(), False
                    self.key = f"switch:{m.group('tid').lower()}"
                elif "Emulation was paused" in line:
                    self.paused = True
                elif "Emulation was resumed" in line:
                    self.paused = False
                elif self.game and ENDED.search(line):
                    self.game, self.paused, self.key = None, False, None
        return self.game, self.paused


def ryujinx_dirs(args: list[str] | None = None) -> list[Path]:
    """Ryujinx's data folder: ~/.config/Ryujinx, or portable next to the binary / AppImage."""
    out = [HOME / ".config" / "Ryujinx"]
    appimage = os.environ.get("APPIMAGE")
    for exe in filter(None, [args[0] if args else None, appimage]):
        out.append(Path(exe).resolve().parent / "portable")
    return [d for d in out if d.is_dir()]


class Detector:
    def __init__(self, shortcuts_source=shortcuts):
        self.ryujinx_logs: dict[str, RyujinxLog] = {}
        self.seen: list[tuple[str, int, list[str]]] = []
        self.paused: str | None = None
        self.shortcuts_source = shortcuts_source
        self.shortcuts: dict[int, dict] = {}
        self.shortcuts_read = 0.0
        self.steam_ids: dict[int, int | None] = {}  # pid -> Steam app id it was started for

    def shortcut(self, appid: int | None) -> dict | None:
        """Re-read shortcuts.vdf at most once a minute, or when an unknown shortcut is started."""
        if appid is None:
            return None
        age = time.time() - self.shortcuts_read
        if (appid not in self.shortcuts and age > 5) or age > 60:
            try:
                self.shortcuts = self.shortcuts_source()
            except Exception:
                self.shortcuts = {}
            self.shortcuts_read = time.time()
        return self.shortcuts.get(appid)

    def detect(self, procs=None) -> tuple[str, str, str | None] | None:
        """(emulator, game, game id) being played right now, or None (also None while a Ryujinx game is paused).
        The game id ("switch:<title id>") lets every language of one game end up under one name."""
        self.seen, self.paused = [], None
        found = None
        live = set()
        for item in (processes() if procs is None else procs):
            pid, comm, args = item[:3]
            entry = match_emulator(comm, args)
            if not entry:
                continue
            name, _, exts = entry
            live.add(pid)
            self.seen.append((name, pid, args))
            if pid not in self.steam_ids:  # a process's environment doesn't change: read it once
                self.steam_ids[pid] = steam_appid(item[3] if len(item) > 3 else environ(pid))
            sc = self.shortcut(self.steam_ids[pid])
            game = game_from_args(args, exts)
            key = switch_key(" ".join(args[1:]) + " " + (sc["launch"] if sc else ""), name)
            if name == "Ryujinx":
                dirs = [d / "Logs" for d in ryujinx_dirs(args)]
                reader = self.ryujinx_logs.setdefault(str(dirs), RyujinxLog(dirs))
                logged, paused = reader.poll()
                if logged and paused:
                    self.paused = logged
                    continue
                game, key = (logged, reader.key) if logged else (game, key)
            if sc and not bad_name(sc["name"]):
                game = sc["name"]  # the name shown in the Steam library
                key = key or f"steamshortcut:{self.steam_ids[pid]}"
            if (game or key) and found is None:
                found = (name, game, key)
        self.steam_ids = {pid: v for pid, v in self.steam_ids.items() if pid in live}
        return found


# --------------------------------------------------------------------------- sessions (same fields as PlayClock)


class Store:
    """This device's sessions, kept in a JSON file and uploaded as PlayClock/devices/<device>.json.gz."""

    def __init__(self, path: Path, device: str):
        self.path = path
        self.device = device
        data = read_json(path, {})
        self.sessions: list[dict] = data.get("sessions", [])
        self.tombstones: list[dict] = data.get("tombstones", [])
        self.keys: dict[str, dict] = data.get("keys", {})   # game id -> {"game", "updated"}
        self.last_upload: str = data.get("last_upload", "")
        self.last_sync: float = data.get("last_sync", 0.0)

    def save(self) -> None:
        write_json(self.path, {"sessions": self.sessions, "tombstones": self.tombstones, "keys": self.keys,
                               "last_upload": self.last_upload, "last_sync": self.last_sync})

    def name_for(self, game: str | None, key: str | None) -> str | None:
        """One name per game id: the first one seen here (stamped -time, so on the PC an earlier or user-chosen
        name wins and this one becomes an alias). Garbled names are never used."""
        if key and key in self.keys:
            return self.keys[key]["game"]
        if bad_name(game):
            return None
        if key:
            self.keys[key] = {"game": game.strip(), "updated": -time.time()}
        return game.strip()

    def start(self, game: str, emulator: str, now: float) -> dict:
        s = {"uid": uuid.uuid4().hex, "game": game, "emulator": emulator, "start": now, "end": now,
             "seconds": 0.0, "source": None, "device": self.device, "updated": time.time()}
        self.sessions.append(s)
        return s

    def recent(self, game: str, since: float) -> dict | None:
        return next((s for s in reversed(self.sessions) if s["game"] == game and s["source"] is None
                     and s["end"] >= since), None)

    def drop(self, s: dict) -> None:
        self.sessions = [x for x in self.sessions if x["uid"] != s["uid"]]
        self.tombstones.append({"uid": s["uid"], "at": time.time()})

    def tracked(self, game: str) -> float:
        return sum(s["seconds"] for s in self.sessions if s["game"] == game and s["source"] is None)

    def import_totals(self, sources: dict[str, list]) -> None:
        """Play time recorded before (or beside) this program: {source: [(title, seconds, last, game id, emulator)]}.
        Ryujinx's own totals and Steam's per-shortcut play time describe the same play, so each game takes the
        larger source (summed within a source), minus what was tracked here. One row per game (uid from
        "<device>|import|<game>"), so re-importing just updates it."""
        best: dict[str, tuple[float, float | None, str]] = {}
        for entries in sources.values():
            per_game: dict[str, tuple[float, float | None, str]] = {}
            for title, secs, last, key, emulator in entries:
                game = self.name_for(title, key)
                if game:
                    prev, prev_last, _ = per_game.get(game, (0.0, None, emulator))
                    per_game[game] = (prev + secs, max(filter(None, (prev_last, last)), default=None), emulator)
            for game, (total, last, emulator) in per_game.items():
                if total > best.get(game, (0.0,))[0]:
                    best[game] = (total, last, emulator)
        for game, (total, last, emulator) in best.items():
            uid = hashlib.sha1(f"{self.device}|import|{game}".encode()).hexdigest()
            add = max(0.0, total - self.tracked(game))
            row = next((s for s in self.sessions if s["uid"] == uid), None)
            if row is None and add <= 0:
                continue
            end = last or time.time()
            fields = {"game": game, "emulator": emulator, "start": end - add, "end": end, "seconds": add,
                      "source": "import", "device": self.device}
            if row is None:
                self.sessions.append({"uid": uid, **fields, "updated": time.time()})
            elif any(row[k] != v for k, v in fields.items()):
                row.update(fields, updated=time.time())

    def payload(self, name: str, open_game: dict | None = None) -> dict:
        """`open_game`: the game Ryujinx has open right now ({"id": title id, "since": t}), so the PC can warn
        before the same game is played there (the Deck may be asleep with it open)."""
        return {"format": FORMAT, "device": self.device, "name": name, "sessions": self.sessions,
                "aliases": [], "tombstones": self.tombstones, "open_game": open_game,
                "keys": [{"key": k, "game": v["game"], "updated": v["updated"]} for k, v in sorted(self.keys.items())]}


class Tracker:
    """Same rules as the Windows tracker: sleep gaps, short sessions, resuming within RESUME_GAP."""

    def __init__(self, store: Store):
        self.store = store
        self.current: dict | None = None
        self.last_tick: float | None = None
        self.saved = 0.0

    def tick(self, now: float, found: tuple | None) -> bool:
        """Advance with (emulator, game[, game id]) or None; returns True when a session ended (sync then)."""
        if found:
            game = self.store.name_for(found[1], found[2] if len(found) > 2 else None)
            found = (found[0], game) if game else None
        dt = 0.0 if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        continuous = 0 < dt <= max(3 * INTERVAL, 30)  # a long gap = the Deck was asleep
        if self.current and found and found[1] == self.current["game"] and continuous:
            self.current["seconds"] += dt
            self.current["end"] = now
            self.current["updated"] = time.time()
            if now - self.saved >= FLUSH_EVERY:
                self.store.save()
                self.saved = now
            return False
        ended = self.stop()
        if found:
            emulator, game = found
            prev = self.store.recent(game, now - RESUME_GAP)
            self.current = prev or self.store.start(game, emulator, now)
            self.current["end"] = now
            self.store.save()
            self.saved = now
        return ended

    def stop(self) -> bool:
        if not self.current:
            return False
        if self.current["seconds"] < MIN_SESSION:
            self.store.drop(self.current)
        self.current = None
        self.store.save()
        return True


def ryujinx_totals() -> list[tuple[str, float, float | None, str | None, str]]:
    """Ryujinx's own per-game play time (games/<titleid>/gui/metadata.json): (title, seconds, last, id, emulator)."""
    out: list[tuple[str, float, float | None, str | None, str]] = []
    for root in ryujinx_dirs():
        for meta in root.glob("games/*/gui/metadata.json"):
            d = read_json(meta, {})
            m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)(?:\.\d+)?", str(d.get("timespan_played", "")).strip())
            title = (d.get("title") or "").strip()
            folder = meta.parent.parent.name
            key = f"switch:{folder.lower()}" if re.fullmatch(r"[0-9a-fA-F]{16}", folder) else None
            if not m or not (title or key):
                continue
            days, h, mi, s = (int(x or 0) for x in m.groups())
            secs = float(((days * 24 + h) * 60 + mi) * 60 + s)
            last = None
            lm = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", d.get("last_played_utc") or "")
            if lm:
                last = float(calendar.timegm(tuple(map(int, lm.groups())) + (0, 0, 0)))
            if secs > 0:
                out.append((title, secs, last, key, "Ryujinx"))
    return out


# --------------------------------------------------------------------------- WebDAV


SyncError = savesync.StorageError


class WebDAV(savesync.DavStorage):
    """The shared WebDAV client (savesync.py, also used for saves) plus this device's file upload."""

    def __init__(self, url: str, user: str, password: str, timeout: float = 20):
        super().__init__(url, user, password, timeout, agent=f"PlayClock-Deck/{VERSION}")

    def upload(self, device: str, blob: bytes) -> None:
        self.ensure_dir(f"{FOLDER}/devices")
        self.put(f"{FOLDER}/devices/{device}.json.gz", blob)


def encode(payload: dict) -> bytes:
    """Byte-identical to the Windows app's sync.encode()."""
    return gzip.compress(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)


# --------------------------------------------------------------------------- Ryujinx save sync


SAVE_EVERY = 15 * 60   # default: while a game runs, upload its save at most this often (always when it closes)
GUARD_FRESH = 60       # a launch is held only if the cloud wasn't checked within this many seconds
# Settings in config.json (the Decky plugin changes them); the agent re-reads the file whenever it changes.
DEFAULTS = {
    "save_sync": False,     # Ryujinx save sync on/off
    "save_every": 15,       # minutes between in-game uploads; 0 = only when the game closes
    "launch_check": True,   # hold a starting Ryujinx while newer cloud saves come down
    "sleep_upload": True,   # upload the open game's save just before the Deck sleeps
    "quota_mb": 1024,       # the storage's monthly upload allowance (0 = no limit)
}


def ryujinx_pids(procs=None) -> list[int]:
    return [p[0] for p in (processes() if procs is None else procs)
            if (m := match_emulator(p[1], p[2])) and m[0] == "Ryujinx"]


def stopped(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"
    except (OSError, IndexError):
        return False


class LaunchGuard(threading.Thread):
    """Steam Cloud downloads before a game starts; EmuDeck starts Ryujinx through Steam, which we can't hook,
    so: a Ryujinx process that just appeared is paused (SIGSTOP) for the second or two it takes to bring a newer
    cloud save down, then continued. Polls only new pids once a second."""

    def __init__(self, agent: "Agent"):
        super().__init__(daemon=True)
        self.agent = agent
        self.known: set[int] = set()

    def run(self) -> None:
        for pid in ryujinx_pids():  # a previous agent may have died while holding one
            if stopped(pid):
                os.kill(pid, signal.SIGCONT)
        active = False
        while True:
            on = self.agent.saves_enabled() and self.agent.setting("launch_check")
            time.sleep(1 if on else 5)
            if not on:
                active = False
                continue
            if not active:  # (re)enabled: processes already running are not new
                self.known, active = {int(d) for d in os.listdir("/proc") if d.isdigit()}, True
                continue
            try:
                self.poll()
            except Exception as e:  # never take the agent down
                log(f"launch guard: {e}")

    def poll(self) -> None:
        pids = {int(d) for d in os.listdir("/proc") if d.isdigit()}
        new = pids - self.known
        self.known = pids
        if not new:
            return
        fresh = []
        for pid in new:
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
                args = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").split("\0")
            except OSError:
                continue
            if (m := match_emulator(comm, args)) and m[0] == "Ryujinx":
                fresh.append(pid)
        if not fresh or time.time() - self.agent.last_save_check < GUARD_FRESH:
            return
        held = []
        try:
            for pid in fresh:
                os.kill(pid, signal.SIGSTOP)
                held.append(pid)
            log(f"Ryujinx starting: holding it while checking the cloud for newer saves")
            self.agent.sync_saves(restore=True, timeout=4, ignore_ryujinx=True, why="launch", quick=True)
        except Exception as e:
            log(f"launch check skipped: {e}")
        finally:
            for pid in held:
                try:
                    os.kill(pid, signal.SIGCONT)
                except OSError:
                    pass


class SleepWatcher(threading.Thread):
    """Best effort Dynamic-Cloud-Sync: just before the Deck sleeps, upload the open game's save if it changed.
    Holds a logind delay inhibitor (at most InhibitDelayMaxSec, 5 s on SteamOS) while doing so."""

    def __init__(self, agent: "Agent"):
        super().__init__(daemon=True)
        self.agent = agent
        self.inhibitor = None

    def hold(self) -> None:
        if self.inhibitor is None or self.inhibitor.poll() is not None:
            self.inhibitor = subprocess.Popen(
                ["systemd-inhibit", "--what=sleep", "--mode=delay", "--who=PlayClock",
                 "--why=Uploading the game save", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def release(self) -> None:
        if self.inhibitor and self.inhibitor.poll() is None:
            self.inhibitor.terminate()
        self.inhibitor = None

    def wanted(self) -> bool:
        return self.agent.saves_enabled() and bool(self.agent.setting("sleep_upload"))

    def apply(self) -> None:
        """Hold the delay inhibitor only while uploads before sleep are wanted (settings may change)."""
        if self.wanted():
            self.hold()
        else:
            self.release()

    def run(self) -> None:
        if not (shutil.which("gdbus") and shutil.which("systemd-inhibit")):
            return
        mon = subprocess.Popen(["gdbus", "monitor", "--system", "--dest", "org.freedesktop.login1",
                                "--object-path", "/org/freedesktop/login1"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.apply()
        for line in mon.stdout:
            if "PrepareForSleep (true" in line:
                try:
                    game = self.agent.open_game
                    if game and self.wanted():
                        self.agent.upload_save(game["id"], in_game=False, timeout=3, why="sleep")
                finally:
                    self.release()
            elif "PrepareForSleep (false" in line:
                self.apply()


# --------------------------------------------------------------------------- app


class Agent:
    def __init__(self, config_path: Path = CONFIG_DIR / "config.json", data_dir: Path = DATA_DIR):
        self.config_path = config_path
        self.cfg = read_json(config_path, {})
        if not self.cfg.get("device_id"):
            self.cfg["device_id"] = uuid.uuid4().hex
            write_json(config_path, self.cfg, private=True)
        self.store = Store(data_dir / "sessions.json", self.cfg["device_id"])
        self.tracker = Tracker(self.store)
        self.detector = Detector()
        self.data_dir = data_dir
        self.save_lock = threading.Lock()   # the main loop, the launch guard and the sleep watcher share saves
        self.last_save_check = 0.0          # last full comparison with the cloud
        self.open_game: dict | None = None  # {"id": title id, "since": t} while Ryujinx has a game loaded
        self.ryujinx_was_running = False
        self.watched: Path | None = None    # the open game's save folder (…/bis/user/save/<index>/0)
        self.watched_sig = ""               # its signature when last uploaded / when the game opened
        self.last_save_upload = 0.0         # in-game uploads: at most one per save_every minutes
        self.config_mtime = self._mtime()
        self.guard: LaunchGuard | None = None
        self.sleeper: SleepWatcher | None = None
        self.wake = threading.Event()       # SIGUSR1 (from the Decky plugin): act now, not at the next poll
        self.last_sync_result = ""
        self.last_action: dict | None = None
        self.live_written = ""

    # --- settings (config.json; the Decky plugin edits it) -------------------------
    def setting(self, key: str):
        return self.cfg.get(key, DEFAULTS[key])

    def _mtime(self) -> float:
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return 0.0

    def reload_config(self) -> set[str]:
        """Re-read config.json if it changed (settings, account, device name); returns the keys that changed."""
        mtime = self._mtime()
        if mtime == self.config_mtime:
            return set()
        self.config_mtime = mtime
        cfg = read_json(self.config_path, None)
        if not isinstance(cfg, dict):
            return set()  # half-written: next poll
        cfg["device_id"] = self.cfg["device_id"]  # never changes
        changed = {k for k in set(cfg) | set(self.cfg) if cfg.get(k) != self.cfg.get(k)}
        self.cfg = cfg
        log("settings changed: " + ", ".join(f"{k}={self.setting(k)}" for k in DEFAULTS))
        self.start_threads()
        return changed

    def start_threads(self) -> None:
        """The launch guard and sleep watcher exist once save sync has been on; they follow the settings."""
        if self.saves_enabled() and self.guard is None:
            self.guard, self.sleeper = LaunchGuard(self), SleepWatcher(self)
            self.guard.start()
            self.sleeper.start()
        elif self.sleeper:
            self.sleeper.apply()

    # --- requests from the Decky plugin (requests.json + SIGUSR1) ------------------
    def handle_requests(self) -> None:
        path = self.data_dir / "requests.json"
        work = path.with_suffix(".work")
        try:
            os.replace(path, work)
        except OSError:
            return  # nothing asked
        requests = read_json(work, [])
        work.unlink(missing_ok=True)
        for req in requests if isinstance(requests, list) else []:
            action = req.get("action")
            try:
                if action == "sync":
                    self.last_sync_result = self.sync(force=True)
                    saves = self.sync_saves(restore=not ryujinx_pids(), why="manual")
                    msg = self.last_sync_result + (f"; saves: {len(saves)} games checked" if saves else "")
                elif action == "resolve":
                    title, keep = req["title"].upper(), req["keep"]
                    with self.save_lock:
                        s = self.make_saves()
                        mine = (s.game(title).get("conflict") or {}).get("file")
                        if keep == "local" and not mine:
                            raise ValueError("this Deck has no conflicting version of that game in the cloud")
                        s.resolve(title, "head" if keep == "cloud" else mine)
                    msg = f"{title}: " + self.sync_saves(restore=not ryujinx_pids(), titles=[title],
                                                          why=f"use {keep}").get(title, "")
                else:
                    continue
                self.last_action = {"action": action, "ok": True, "message": msg, "time": time.time()}
            except Exception as e:  # reported to the plugin, never fatal
                self.last_action = {"action": action, "ok": False, "message": str(e), "time": time.time()}
            log(f"request {action}: {self.last_action['message']}")

    # --- what the Decky plugin shows (live.json, written only when it changes) -----
    def today_seconds(self) -> float:
        lt = time.localtime()
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return sum(min(s["seconds"], s["end"] - midnight) for s in self.store.sessions
                   if s["source"] is None and s["end"] > midnight)

    def write_live(self) -> None:
        cur = self.tracker.current
        live = {"version": VERSION, "pid": os.getpid(), "name": self.name,
                "playing": {"game": cur["game"], "emulator": cur["emulator"], "start": cur["start"],
                            "minutes": int(cur["seconds"] // 60)} if cur else None,
                "paused": self.detector.paused, "today_minutes": int(self.today_seconds() // 60),
                "last_sync": self.store.last_sync, "sync_result": self.last_sync_result,
                "save_check": self.last_save_check, "last_action": self.last_action}
        text = json.dumps(live, ensure_ascii=False, sort_keys=True)
        if text != self.live_written:  # minutes, not seconds: at most about one write a minute while playing
            write_json(self.data_dir / "live.json", dict(live, updated=time.time()))
            self.live_written = text

    @property
    def name(self) -> str:
        return self.cfg.get("name") or socket.gethostname() or "Steam Deck"

    def client(self) -> WebDAV | None:
        w = self.cfg.get("webdav")
        return WebDAV(w["url"], w["user"], w["password"]) if w else None

    def sync(self, force: bool = False) -> str:
        """Import earlier play time (Ryujinx, Steam shortcuts), then upload this device's file if it changed."""
        sources = {}
        for source, read in (("ryujinx", ryujinx_totals), ("steam", shortcut_totals)):
            try:
                sources[source] = read()
            except Exception as e:  # a file in an unexpected shape must not stop recording
                log(f"{source} import skipped: {e}")
        self.store.import_totals(sources)
        client = self.client()
        if not client:
            return "not set up (run: playclock_deck.py setup)"
        blob = encode(self.store.payload(self.name, self.open_game))
        digest = hashlib.sha1(blob).hexdigest()
        if digest == self.store.last_upload and not force:
            self.store.last_sync = time.time()
            self.store.save()
            return "up to date"
        try:
            client.upload(self.store.device, blob)
        except SyncError as e:
            return f"sync failed: {e}"
        self.store.last_upload, self.store.last_sync = digest, time.time()
        self.store.save()
        return f"uploaded {len(blob)} bytes, {len(self.store.sessions)} sessions"

    # --- Ryujinx save sync (playclock/savesync.py) ---------------------------------
    def save_root(self) -> Path | None:
        return next((d for d in ryujinx_dirs() if (d / "bis" / "user" / "save").is_dir()), None)

    def saves_enabled(self) -> bool:
        return bool(self.cfg.get("save_sync") and self.cfg.get("webdav") and self.save_root())

    def make_saves(self, timeout: float = 20, ignore_ryujinx: bool = False):
        w = self.cfg["webdav"]
        storage = WebDAV(w["url"], w["user"], w["password"], timeout=timeout)
        busy = (lambda: False) if ignore_ryujinx else (lambda: bool(ryujinx_pids()))
        quota = int(self.setting("quota_mb")) * 2 ** 20 or 1 << 62  # 0: no limit
        return savesync.SaveSync(self.save_root(), storage, CONFIG_DIR / "save_sync.json",
                                 self.data_dir / "save_backups" / "ryujinx", self.store.device, self.name, busy=busy,
                                 quota_up=quota, cloud_only=False)  # games not on this Deck: not read at all

    def sync_saves(self, restore: bool = True, titles: list[str] | None = None, timeout: float = 20,
                   ignore_ryujinx: bool = False, why: str = "", quick: bool = False) -> dict:
        """Compare with the cloud: upload what changed here, restore what changed there (Ryujinx closed only)."""
        if not self.saves_enabled():
            return {}
        with self.save_lock:
            try:
                result = self.make_saves(timeout, ignore_ryujinx).sync(allow_restore=restore, titles=titles,
                                                                       quick=quick)
            except savesync.StorageError as e:
                log(f"saves ({why}): {e}")
                return {}
            if titles is None:
                self.last_save_check = time.time()
        moved = {t: s for t, s in result.items() if s not in ("synced", "needs_local")}
        if moved:
            log(f"saves ({why}): " + ", ".join(f"{t} {s}" for t, s in moved.items()))
        return result

    def upload_save(self, title: str, in_game: bool, timeout: float = 20, why: str = "") -> bool:
        """Upload one game's save if it changed. in_game: skipped above 70 % of the month's upload allowance."""
        if not self.saves_enabled():
            return False
        with self.save_lock:
            try:
                s = self.make_saves(timeout)
                if in_game and s.degraded():
                    return False
                done = s.upload_if_changed(title)
            except savesync.StorageError as e:
                log(f"save upload ({why}) {title}: {e}")
                return False
        if done:
            log(f"save uploaded ({why}): {title}")
        return done

    def loaded_game(self) -> str | None:
        """Title id of the game Ryujinx has loaded (paused too), from its log."""
        for reader in self.detector.ryujinx_logs.values():
            if reader.game and reader.key and reader.key.startswith("switch:"):
                return reader.key[7:].upper()
        return None

    @staticmethod
    def signature(folder: Path | None) -> str:
        try:
            return savesync.tree_signature(folder) if folder else ""
        except OSError:
            return ""

    def watch_saves(self, woke: bool) -> bool:
        """Per poll. Returns True when the device file should be uploaded (the open game changed)."""
        running = any(name == "Ryujinx" for name, _, _ in self.detector.seen)
        game = self.loaded_game() if running else None
        before = self.open_game["id"] if self.open_game else None
        changed = game != before
        if changed:
            self.open_game = {"id": game, "since": time.time()} if game else None
        if not self.saves_enabled():
            self.ryujinx_was_running = running
            return changed
        if before and before != game:
            self.sync_saves(restore=False, titles=[before], why="game closed")   # like Steam: upload on exit
        if changed:  # watch the open game's save folder: its first change goes up at once
            sd = self.make_saves().local().get(game) if game else None
            self.watched = sd.path / "0" if sd else None
            self.watched_sig = self.signature(self.watched)
            self.last_save_upload = 0.0
        every = int(self.setting("save_every")) * 60  # 0: only when the game closes
        if game and self.watched and every:
            sig = self.signature(self.watched)  # stat calls only, every poll
            if sig != self.watched_sig and time.time() - self.last_save_upload >= every:
                if self.upload_save(game, in_game=True, why="while playing"):
                    self.open_game["saved"] = time.time()  # tells the PC (device file) to fetch it
                    changed = True                         # -> the device file is uploaded right away
                self.watched_sig, self.last_save_upload = sig, time.time()  # failures retry 15 minutes later
        if (self.ryujinx_was_running and not running) or (woke and not running):
            self.sync_saves(restore=True, why="ryujinx closed" if not woke else "woke up")
        elif woke:
            self.sync_saves(restore=False, why="woke up")  # at least send what was left un-uploaded
        self.ryujinx_was_running = running
        return changed

    def run(self) -> None:
        log(f"PlayClock Deck {VERSION} running as device {self.store.device} ({self.name})")
        signal.signal(signal.SIGUSR1, lambda *_: self.wake.set())
        self.start_threads()  # not started while save sync is off
        if self.saves_enabled():
            self.sync_saves(restore=not ryujinx_pids(), why="start")
        last_sync = 0.0
        while True:
            changed = self.reload_config()
            if changed & {"save_sync", "webdav"} and self.saves_enabled():  # turned on / another account
                self.sync_saves(restore=not ryujinx_pids(), why="settings")
            self.handle_requests()
            now = time.time()
            woke = self.tracker.last_tick is not None and now - self.tracker.last_tick > 60  # back from sleep
            found = self.detector.detect()
            current = self.tracker.current
            ended = self.tracker.tick(now, found)
            if self.tracker.current and self.tracker.current is not current:
                log(f"playing {self.tracker.current['game']} ({self.tracker.current['emulator']})")
            try:
                open_changed = self.watch_saves(woke)
            except Exception as e:  # saves must never stop play-time recording
                log(f"saves: {e}")
                open_changed = False
            if ended or woke or open_changed or now - last_sync >= SYNC_EVERY:
                self.last_sync_result = self.sync()
                log(self.last_sync_result)
                last_sync = now
            self.write_live()
            self.wake.wait(INTERVAL)
            self.wake.clear()


def cmd_setup(agent: Agent) -> None:
    print("PlayClock Deck setup — your play time syncs to your own WebDAV storage (坚果云 by default).")
    print("坚果云: use an APP password (desktop client: Settings > Third-party apps), not your login password.")
    url = input(f"WebDAV server [{JIANGUOYUN}]: ").strip() or JIANGUOYUN
    user = input("Account (坚果云 login email / phone): ").strip()
    password = getpass.getpass("App password (not shown): ")
    name = input(f"Name for this device [{agent.name}]: ").strip() or agent.name
    try:
        WebDAV(url, user, password).check()
    except SyncError as e:
        sys.exit("Could not connect: wrong account or app password." if str(e) == "auth" else f"Could not connect: {e}")
    agent.cfg.update(webdav={"url": url, "user": user, "password": password}, name=name)
    write_json(agent.config_path, agent.cfg, private=True)  # mode 600: only this user can read it
    print("Connected. " + agent.sync(force=True))


def cmd_status(agent: Agent) -> None:
    found = agent.detector.detect()
    print(f"PlayClock Deck {VERSION} · device {agent.store.device} ({agent.name})")
    print("emulators running:", ", ".join(sorted({n for n, _, _ in agent.detector.seen})) or "none")
    print("playing now:", f"{found[1] or found[2]} ({found[0]})" if found else
          (f"{agent.detector.paused} (paused)" if agent.detector.paused else "nothing"))
    tracked = [s for s in agent.store.sessions if s["source"] is None]
    print(f"sessions recorded: {len(tracked)}, {sum(s['seconds'] for s in tracked) / 3600:.1f} h")
    last = agent.store.last_sync
    print("last sync:", time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "never",
          "· WebDAV:", (agent.cfg.get("webdav") or {}).get("user", "not set up"))


def cmd_doctor(agent: Agent) -> None:
    print(f"PlayClock Deck {VERSION} · Python {sys.version.split()[0]} · {os.uname().sysname} {os.uname().release}")
    print("config:", agent.config_path, "· data:", agent.store.path)
    for d in [HOME / ".config" / "Ryujinx", HOME / "Emulation", HOME / "Applications"]:
        print(f"{'found  ' if d.exists() else 'missing'} {d}")
    for root in ryujinx_dirs():
        logs = sorted(root.glob("Logs/*.log"), key=lambda f: f.stat().st_mtime)
        print(f"Ryujinx data {root}: {len(list(root.glob('games/*/gui/metadata.json')))} games with metadata,"
              f" newest log: {logs[-1].name if logs else 'none'}")
    print("Ryujinx totals:", {t or k: round(sec / 3600, 2) for t, sec, _l, k, _e in ryujinx_totals()} or "none")
    print("Steam roots:", [str(r) for r in steam_roots()] or "none")
    scs, times = shortcuts(), shortcut_playtimes()
    print(f"Steam shortcuts: {len(scs)} (emulator ones marked; play time = Steam's own record)")
    for appid, sc in sorted(scs.items(), key=lambda kv: kv[1]["name"].lower()):
        entry = shortcut_emulator(sc)
        secs = times.get(appid, (0.0, None))[0]
        print(f"  {'*' if entry else ' '} {appid:>10} {sc['name'][:40]:<40} [{entry[0] if entry else '-'}]"
              f" {secs / 3600:.1f} h  exe={sc['exe'][-50:]!r} launch={sc['launch'][-60:]!r}")
    print("Steam play time entries that look like non-Steam shortcuts:",
          {a: round(t[0] / 3600, 2) for a, t in times.items() if a in scs} or "none")
    print("emulator-like processes:")
    hint = re.compile("|".join(p for _, p, _ in EMULATORS) + r"|emu|appimage", re.I)
    for pid, comm, args in processes():
        if hint.search(comm + " " + " ".join(args[:2])):
            entry = match_emulator(comm, args)
            env = environ(pid)
            print(f"  [{entry[0] if entry else '?'}] pid {pid} comm={comm!r} SteamGameId={env.get('SteamGameId')}"
                  f" SteamAppId={env.get('SteamAppId')} argv={args[:6]}")
    found = agent.detector.detect()
    print("detected now:", found or (f"{agent.detector.paused} (paused)" if agent.detector.paused else "nothing"))


def cmd_saves(agent: Agent, args: list[str]) -> None:
    """saves [status] | on | off | sync | use-cloud <title id> | use-local <title id>"""
    what = args[0] if args else "status"
    if what in ("on", "off"):
        agent.cfg["save_sync"] = what == "on"
        write_json(agent.config_path, agent.cfg, private=True)
        print(f"Ryujinx save sync {what} (the running service picks it up within a few seconds).")
        return
    if not agent.saves_enabled():
        sys.exit("Save sync is off, not set up, or Ryujinx's save folder is missing (try: saves on).")
    if what == "sync":
        for title, status in agent.sync_saves(restore=not ryujinx_pids(), why="manual").items():
            print(f"{title} {status}")
        return
    if what in ("use-cloud", "use-local") and len(args) > 1:
        title = args[1].upper()
        with agent.save_lock:
            s = agent.make_saves()
            mine = (s.game(title).get("conflict") or {}).get("file")
            if what == "use-local" and not mine:
                sys.exit("This device has no conflicting version of that game in the cloud.")
            s.resolve(title, "head" if what == "use-cloud" else mine)
        print(agent.sync_saves(restore=not ryujinx_pids(), titles=[title], why=what))
        return
    s = agent.make_saves()
    names = savesync.game_names(s.root)
    up, down = s.traffic()
    print(f"saves: {s.root} · user {s.user} · this month {up / 2**20:.1f} MB up, {down / 2**20:.1f} MB down"
          f"{' · in-game uploads paused (over 70 % of the allowance)' if s.degraded() else ''}")
    for title, g in sorted(s.state["games"].items()):
        head = g.get("head") or {}
        cloud = f" · cloud: {head.get('device_name', '?')} {time.strftime('%m-%d %H:%M', time.localtime(head['time']))}" \
            if head.get("time") else ""
        print(f"  {title} {names.get(title, '')[:30]:<30} {g.get('status', '?')}{cloud}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="PlayClock for Steam Deck: records emulator play time.")
    ap.add_argument("command", choices=["setup", "run", "status", "sync", "doctor", "saves"])
    ap.add_argument("args", nargs="*")
    args = ap.parse_args(argv)
    agent = Agent()
    if args.command == "saves":
        cmd_saves(agent, args.args)
        return
    if args.command == "setup":
        cmd_setup(agent)
    elif args.command == "run":
        agent.run()
    elif args.command == "status":
        cmd_status(agent)
    elif args.command == "sync":
        print(agent.sync(force=True))
    else:
        cmd_doctor(agent)


if __name__ == "__main__":
    main()
