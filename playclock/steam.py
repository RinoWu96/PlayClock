"""Read Steam's own playtime records from the local Steam install (no account, key or network).

- userdata/<accountid>/config/localconfig.vdf (text KeyValues): per app "Playtime" (minutes) and "LastPlayed"
- appcache/appinfo.vdf (binary KeyValues, v28/v29): app names, localized names and types
"""
import re
import struct
import winreg
from dataclasses import dataclass
from pathlib import Path

from .importers import EmuGame

SKIP_TYPES = {"tool", "application", "config", "dlc", "demo", "music", "video", "hardware", "beta"}


@dataclass
class SteamAccount:
    account_id: str
    persona: str
    config: Path  # localconfig.vdf


# --------------------------------------------------------------------------- locating Steam


def steam_root() -> Path | None:
    candidates = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            candidates.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]))
    except OSError:
        pass
    candidates += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    return next((c for c in candidates if (c / "userdata").is_dir()), None)


def accounts(root: Path) -> list[SteamAccount]:
    """Steam accounts that have logged in on this PC, most recently used first."""
    out = []
    for cfg in (root / "userdata").glob("*/config/localconfig.vdf"):
        try:
            head = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"PersonaName"\s+"((?:[^"\\]|\\.)*)"', head)
        out.append(SteamAccount(cfg.parent.parent.name, m.group(1) if m else cfg.parent.parent.name, cfg))
    return sorted(out, key=lambda a: a.config.stat().st_mtime, reverse=True)


# --------------------------------------------------------------------------- text KeyValues


def parse_vdf(text: str) -> dict:
    """Text KeyValues -> nested dicts (keys lower-cased)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root: dict = {}
    stack = [root]
    key = None
    for s, brace in tokens:
        if brace == "{":
            new: dict = {}
            stack[-1][key.lower()] = new
            stack.append(new)
            key = None
        elif brace == "}":
            stack.pop()
        elif key is None:
            key = s
        else:
            stack[-1][key.lower()] = s.replace('\\"', '"').replace("\\\\", "\\")
            key = None
    return root


def playtimes(config: Path) -> dict[int, tuple[float, float | None]]:
    """appid -> (seconds, last played epoch) for apps with recorded playtime."""
    kv = parse_vdf(config.read_text(encoding="utf-8", errors="replace"))
    apps = kv.get("userlocalconfigstore", {}).get("software", {}).get("valve", {}).get("steam", {}).get("apps", {})
    out = {}
    for appid, d in apps.items():
        if not isinstance(d, dict) or not appid.isdigit():
            continue
        minutes = int(d.get("playtime", "0") or 0)
        if minutes > 0:
            last = int(d.get("lastplayed", "0") or 0)
            out[int(appid)] = (minutes * 60.0, float(last) if last else None)
    return out


# --------------------------------------------------------------------------- binary appinfo.vdf


def read_appinfo(path: Path) -> dict[int, tuple[str, str, dict]]:
    """appid -> (name, type, localized names) from Steam's app cache."""
    data = path.read_bytes()
    magic, _universe = struct.unpack_from("<II", data, 0)
    if magic not in (0x07564428, 0x07564429):
        raise ValueError(f"unsupported appinfo.vdf version {magic:#x}")
    v29 = magic == 0x07564429
    pos = 8
    strings: list[str] = []
    if v29:  # keys are indices into a string table at the end of the file
        (table,) = struct.unpack_from("<q", data, pos)
        pos += 8
        (count,) = struct.unpack_from("<I", data, table)
        strings = [s.decode("utf-8", "replace") for s in data[table + 4:].split(b"\0", count)[:count]]

    def cstr(i):
        j = data.index(b"\0", i)
        return data[i:j].decode("utf-8", "replace"), j + 1

    def read_kv(i):
        out = {}
        while True:
            t = data[i]
            i += 1
            if t == 0x08:
                return out, i
            if v29:
                key = strings[struct.unpack_from("<I", data, i)[0]]
                i += 4
            else:
                key, i = cstr(i)
            if t == 0x00:
                out[key], i = read_kv(i)
            elif t == 0x01:
                out[key], i = cstr(i)
            elif t in (0x02, 0x03, 0x06):
                out[key] = struct.unpack_from("<f" if t == 0x03 else "<i", data, i)[0]
                i += 4
            elif t in (0x07, 0x0A):
                out[key] = struct.unpack_from("<Q", data, i)[0]
                i += 8
            else:
                raise ValueError(f"unknown KeyValues type {t:#x}")

    apps = {}
    while True:
        appid, size = struct.unpack_from("<II", data, pos)
        if appid == 0:
            break
        # size, info_state, last_updated, pics_token, sha1, change_number, binary_sha1 precede the KeyValues
        kv, _ = read_kv(pos + 8 + 4 + 4 + 8 + 20 + 4 + 20)
        common = kv.get("appinfo", {}).get("common", {})
        if common.get("name"):
            apps[appid] = (common["name"], str(common.get("type", "")).lower(), common.get("name_localized") or {})
        pos += 8 + size
    return apps


def name_variants(root: Path) -> dict[int, list[str]]:
    """appid -> every name Steam has for it (default + localized), so names saved in another language can be
    matched back to the game."""
    try:
        info = read_appinfo(root / "appcache" / "appinfo.vdf")
    except (OSError, ValueError, struct.error, IndexError):
        return {}
    return {appid: list(dict.fromkeys([name, *[v for v in localized.values() if isinstance(v, str)]]))
            for appid, (name, _kind, localized) in info.items()}


# --------------------------------------------------------------------------- import


def scan_steam(root: Path, account: SteamAccount, lang: str = "zh",
               known_names: dict[int, str] | None = None) -> tuple[list[EmuGame], list[int]]:
    """Games with playtime -> (games, appids whose name is unknown)."""
    try:
        info = read_appinfo(root / "appcache" / "appinfo.vdf")
    except (OSError, ValueError, struct.error, IndexError):
        info = {}
    known_names = known_names or {}
    games, unknown = [], []
    for appid, (secs, last) in playtimes(account.config).items():
        if appid in info:
            name, kind, localized = info[appid]
            if kind in SKIP_TYPES:
                continue
            if lang == "zh":
                name = localized.get("schinese") or localized.get("tchinese") or name
        elif appid in known_names:
            name = known_names[appid]
        else:
            unknown.append(appid)
            continue
        games.append(EmuGame(name.strip(), secs, last, header_art(root, appid), key=f"steam:{appid}"))
    return games, unknown


def header_art(root: Path, appid: int) -> Path | str:
    """Steam's store header (460x215) for an app: the client's local cache, else Steam's public CDN."""
    folder = root / "appcache" / "librarycache" / str(appid)
    for p in (folder / "header.jpg", *folder.glob("*/header.jpg")):
        if p.is_file():
            return p
    return f"https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
