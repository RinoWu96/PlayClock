"""Ryujinx save sync between devices through the user's own cloud storage (docs/SAVE_SYNC_DESIGN.md).

Standard library only: the Windows app and the Steam Deck agent (deck/playclock_deck.py) both use this file.

Ryujinx keeps each save in bis/user/save/<index>/ with the save itself in 0/ (1/ is a working copy with the same
content) and ExtraData0 saying which game, user and save type it is. The index is assigned per install, so the
same game has different indexes on the PC and on the Deck: saves are matched by game (title) id instead.

Remote layout, one folder per game:
    PlayClock/saves/ryujinx/<TITLE ID>/head.json        which version is current (+ hashes users decided against)
    PlayClock/saves/ryujinx/<TITLE ID>/v-<UTC>-<device>-<hash8>.zip              the last KEEP_VERSIONS versions
    PlayClock/saves/ryujinx/<TITLE ID>/conflict-<UTC>-<device>-<hash>.zip   another device's diverging version

Each device remembers the hash it last agreed with the cloud ("base"). With L = local hash, B = base and
R = the cloud's head: only the cloud changed -> restore it (after a local backup); only this device changed ->
upload; both changed -> conflict: the local version is uploaded as a conflict file and nothing local is touched
until a user picks one version (on any device); the hashes picked against go into head["supersedes"], so the
device holding one of them knows to take the cloud version.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

REMOTE = "PlayClock/saves/ryujinx"
KEEP_VERSIONS = 5          # cloud versions per game
KEEP_BACKUPS = 20          # local backups per game (taken before every overwrite)
KEEP_SUPERSEDED = 20       # hashes remembered in head["supersedes"]
MAX_SAVE = 256 * 1024 * 1024
TYPE_ACCOUNT = 1
QUOTA_UP = 1024 ** 3       # monthly upload allowance of the storage (坚果云 free: 1 GB)
DEGRADE_AT = 0.7           # above this share of QUOTA_UP, in-game uploads stop (uploads on exit continue)
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


# --------------------------------------------------------------------------- Ryujinx's save folders


@dataclass
class SaveDir:
    index: str          # folder name under bis/user/save, e.g. "0000000000000003"
    path: Path
    title: str          # 16 upper-case hex digits
    user: str           # 32 hex digits, as in Profiles.json
    type: int           # 1 = account save, 3 = device save, 2 = BCAT …


def parse_extra(data: bytes) -> tuple[str, str, int]:
    """(title id, user id, save type) from the start of ExtraData0 (little-endian)."""
    if len(data) < 0x21:
        raise ValueError("ExtraData0 too short")
    title = int.from_bytes(data[0:8], "little")
    user_lo, user_hi = int.from_bytes(data[8:16], "little"), int.from_bytes(data[16:24], "little")
    return f"{title:016X}", f"{user_lo:016x}{user_hi:016x}", data[0x20]


def save_dirs(root: Path) -> list[SaveDir]:
    out = []
    base = root / "bis" / "user" / "save"
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        try:
            title, user, typ = parse_extra((d / "ExtraData0").read_bytes())
        except (OSError, ValueError):
            continue
        if (d / "0").is_dir():
            out.append(SaveDir(d.name, d, title, user, typ))
    return out


def profile_user(root: Path) -> str:
    """The open Ryujinx profile's user id (Ryujinx's default RyuPlayer if the file can't be read)."""
    try:
        profiles = json.loads((root / "system" / "Profiles.json").read_text(encoding="utf-8"))["profiles"]
        chosen = next((p for p in profiles if p.get("account_state") == "Open"), profiles[0])
        return chosen["user_id"].lower()
    except (OSError, ValueError, KeyError, IndexError):
        return "00000000000000010000000000000000"


def game_names(root: Path) -> dict[str, str]:
    """title id -> the title Ryujinx shows (games/<id>/gui/metadata.json)."""
    out = {}
    for meta in (root / "games").glob("*/gui/metadata.json"):
        try:
            title = json.loads(meta.read_text(encoding="utf-8")).get("title", "").strip()
        except (OSError, ValueError):
            continue
        # Ryujinx sometimes stores "<Unknown Name>" or undecoded bytes: not a name
        if title and title != "<Unknown Name>" and "�" not in title and not any(ord(c) < 32 for c in title):
            out[meta.parent.parent.name.upper()] = title
    return out


# --------------------------------------------------------------------------- hashing and packing


def read_tree(folder: Path) -> dict[str, bytes]:
    files = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            files[p.relative_to(folder).as_posix()] = p.read_bytes()
    return files


def files_hash(files: dict[str, bytes]) -> str:
    """Content hash of a save: paths, sizes and contents (not timestamps, not ExtraData)."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{rel}\0{len(files[rel])}\0{hashlib.sha256(files[rel]).hexdigest()}\n".encode())
    return h.hexdigest()


def tree_signature(folder: Path) -> str:
    """Cheap change check (stat calls only): file count, total size, newest modification time."""
    n = size = newest = 0
    for p in folder.rglob("*"):
        if p.is_file():
            st = p.stat()
            n, size, newest = n + 1, size + st.st_size, max(newest, st.st_mtime_ns)
    return f"{n}-{size}-{newest}"


def pack(files: dict[str, bytes], meta: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            z.writestr(zipfile.ZipInfo("save/" + rel, ZIP_TIME), files[rel], zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("meta.json", ZIP_TIME), json.dumps(meta, ensure_ascii=False, indent=1))
    return buf.getvalue()


def unpack(blob: bytes) -> tuple[dict[str, bytes], dict]:
    """(files, meta) from a save zip; refuses anything that could write outside the save folder."""
    files, meta, total = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            name = info.filename
            if name == "meta.json":
                meta = json.loads(z.read(info).decode("utf-8"))
                continue
            if info.is_dir():
                continue
            rel = name[len("save/"):] if name.startswith("save/") else None
            parts = rel.split("/") if rel else []
            if (not rel or "\\" in rel or ":" in rel or rel.startswith("/")
                    or any(p in ("", ".", "..") for p in parts)):
                raise ValueError(f"unsafe path in save zip: {name!r}")
            total += info.file_size
            if total > MAX_SAVE or len(files) > 20000:
                raise ValueError("save zip too large")
            files[rel] = z.read(info)
    return files, meta


def utc_stamp(t: float) -> str:
    """Sortable UTC time with milliseconds: file names order versions, so seconds alone could tie."""
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(t)) + f"{int(t * 1000) % 1000:03d}Z"


# --------------------------------------------------------------------------- storage (WebDAV now; OneDrive later)


class StorageError(Exception):
    pass


class Precondition(StorageError):
    """The file changed since it was read (If-Match failed)."""


class DavStorage:
    """The few operations save sync needs, over WebDAV (坚果云 by default). Counts bytes moved."""

    def __init__(self, url: str, user: str, password: str, timeout: float = 30, agent: str = "PlayClock"):
        self.base = url.rstrip("/") + "/"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout, self.agent = timeout, agent
        self.up = self.down = 0
        self.made: set[str] = set()
        self._count = threading.Lock()  # sync(quick=True) fetches in parallel

    def _req(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
        url = self.base + "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)
        if method == "PROPFIND" and not url.endswith("/"):
            url += "/"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": self.auth, "User-Agent": self.agent, **(headers or {})})
        with self._count:
            self.up += len(data or b"")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                with self._count:
                    self.down += len(body)
                return r.status, body, r.headers
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise StorageError("auth") from e
            return e.code, e.read(), e.headers
        except (urllib.error.URLError, OSError) as e:
            raise StorageError(f"network: {e}") from e

    def check(self) -> None:
        """Can we log in? Raises StorageError("auth") for a wrong account / app password."""
        status, _, _ = self._req("PROPFIND", "", headers={"Depth": "0"})
        if status not in (200, 207):
            raise StorageError(f"HTTP {status}")

    def ensure_dir(self, folder: str) -> None:
        parts = folder.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in self.made:
                continue
            status, _, _ = self._req("MKCOL", sub)
            if status not in (200, 201, 405):  # 405 = already there
                raise StorageError(f"MKCOL {sub}: HTTP {status}")
            self.made.add(sub)

    def entries(self, folder: str) -> tuple[dict[str, str], list[str]]:
        """({file name: version tag}, [sub-folder names]) directly in `folder`."""
        status, body, _ = self._req("PROPFIND", folder, headers={"Depth": "1"})
        if status == 404:
            return {}, []
        if status not in (200, 207):
            raise StorageError(f"PROPFIND {folder}: HTTP {status}")
        ns = {"d": "DAV:"}
        own = urllib.parse.unquote(urllib.parse.urlparse(self.base).path).rstrip("/") + "/" + folder.strip("/")
        files, dirs = {}, []
        for resp in ET.fromstring(body).findall("d:response", ns):
            href = urllib.parse.unquote(urllib.parse.urlparse(resp.findtext("d:href", "", ns)).path).rstrip("/")
            if href == own.rstrip("/"):
                continue
            name = href.rsplit("/", 1)[-1]
            prop = resp.find(".//d:prop", ns)
            if prop is not None and prop.find("d:resourcetype/d:collection", ns) is not None:
                dirs.append(name)
            elif prop is not None:
                files[name] = (prop.findtext("d:getetag", "", ns) or
                               prop.findtext("d:getlastmodified", "", ns) + prop.findtext("d:getcontentlength", "", ns))
        return files, dirs

    def get(self, path: str) -> tuple[bytes, str] | None:
        status, body, headers = self._req("GET", path)
        if status == 404:
            return None
        if status != 200:
            raise StorageError(f"GET {path}: HTTP {status}")
        return body, headers.get("ETag", "") if headers else ""

    def put(self, path: str, data: bytes, if_match: str | None = None, if_none_match: bool = False) -> None:
        headers = {"Content-Type": "application/octet-stream"}
        if if_match:
            headers["If-Match"] = if_match
        elif if_none_match:
            headers["If-None-Match"] = "*"
        status, _, _ = self._req("PUT", path, data, headers)
        if status == 412:
            raise Precondition(path)
        if status not in (200, 201, 204):
            raise StorageError(f"PUT {path}: HTTP {status}")

    def delete(self, path: str) -> None:
        status, _, _ = self._req("DELETE", path)
        if status not in (200, 204, 404):
            raise StorageError(f"DELETE {path}: HTTP {status}")


# --------------------------------------------------------------------------- the decision


def decide(local: str | None, base: str | None, head: dict | None) -> str:
    """upload / restore / conflict / synced / needs_local (no save for this game on this device yet) / none."""
    remote = head["hash"] if head else None
    if remote is None:
        return "upload" if local else "none"
    if local is None:
        return "needs_local"
    if local == remote:
        return "synced"
    if (base is not None and local == base) or local in head.get("supersedes", []):
        return "restore"
    if base is not None and remote == base:
        return "upload"
    return "conflict"


# --------------------------------------------------------------------------- one device's view


class Busy(Exception):
    """A game is running: saves must not be written now."""


class SaveSync:
    """Save sync for one Ryujinx data folder on one device.

    `busy()` must say whether a Ryujinx game may have the saves open; it is asked again right before any write."""

    def __init__(self, root: Path, storage, state_path: Path, backup_dir: Path, device: str, device_name: str,
                 busy=lambda: False, user: str | None = None, quota_up: int = QUOTA_UP):
        self.root, self.storage, self.state_path, self.backup_dir = Path(root), storage, Path(state_path), backup_dir
        self.device, self.device_name, self.busy = device, device_name, busy
        self.user = (user or profile_user(self.root)).lower()
        self.quota_up = quota_up
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("games", {})
        self._names: dict[str, str] | None = None
        self._seen = (0, 0)  # storage byte counters already added to this month's traffic

    # --- state -----------------------------------------------------------------
    def save_state(self) -> None:
        self._count_traffic()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _count_traffic(self) -> None:
        """Add what the storage moved since last time to this month's totals (what the provider meters)."""
        month = time.strftime("%Y-%m")
        t = self.state.get("traffic") or {}
        if t.get("month") != month:
            t = {"month": month, "up": 0, "down": 0}
        up, down = getattr(self.storage, "up", 0), getattr(self.storage, "down", 0)
        t["up"] += up - self._seen[0]
        t["down"] += down - self._seen[1]
        self._seen = (up, down)
        self.state["traffic"] = t

    def traffic(self) -> tuple[int, int]:
        self._count_traffic()
        t = self.state["traffic"]
        return t["up"], t["down"]

    def degraded(self) -> bool:
        """Over DEGRADE_AT of this month's upload allowance: only upload when a game is closed."""
        return self.traffic()[0] >= DEGRADE_AT * self.quota_up

    def game(self, title: str) -> dict:
        return self.state["games"].setdefault(title, {})

    def names(self) -> dict[str, str]:
        if self._names is None:
            self._names = game_names(self.root)
        return self._names

    # --- local saves -----------------------------------------------------------
    def local(self) -> dict[str, SaveDir]:
        """This profile's account saves, by title id."""
        return {s.title: s for s in save_dirs(self.root) if s.type == TYPE_ACCOUNT and s.user == self.user}

    def local_hash(self, s: SaveDir) -> str:
        g = self.game(s.title)
        sig = tree_signature(s.path / "0")
        cached = g.get("scan") or {}
        if cached.get("sig") == sig and cached.get("index") == s.index:
            return cached["hash"]
        h = files_hash(read_tree(s.path / "0"))
        g["scan"] = {"sig": sig, "index": s.index, "hash": h}
        return h

    # --- remote ----------------------------------------------------------------
    def folder(self, title: str) -> str:
        return f"{REMOTE}/{title}"

    def head(self, title: str) -> tuple[dict | None, str]:
        got = self.storage.get(f"{self.folder(title)}/head.json")
        if got is None:
            return None, ""
        return json.loads(got[0].decode("utf-8")), got[1]

    def remote_titles(self) -> list[str]:
        return sorted(self.storage.entries(REMOTE)[1])

    def meta(self, title: str, h: str, size: int) -> dict:
        return {"title": title, "game": self.names().get(title, ""), "user": self.user, "hash": h, "size": size,
                "device": self.device, "device_name": self.device_name, "time": time.time()}

    # --- one game --------------------------------------------------------------
    def sync_game(self, title: str, s: SaveDir | None = None, allow_restore: bool = True,
                  prefetched: tuple | None = None) -> str:
        """Bring one game in line with the cloud; returns what happened (the decide() word, or "busy").
        `prefetched`: (head, etag) already fetched (sync(quick=True) gets them all in parallel)."""
        s = s or self.local().get(title)
        g = self.game(title)
        local = self.local_hash(s) if s else None
        head, etag = prefetched if prefetched is not None else self.head(title)
        action = decide(local, g.get("base"), head)
        status = action
        try:
            if action == "synced":
                g["base"] = local
            elif action == "upload":
                head = self._upload(title, s, head, etag)
            elif action == "restore":
                if allow_restore:
                    self._restore(title, s, head)
                else:
                    status = "pending_restore"
            elif action == "conflict":
                self._push_conflict(title, s, local)
        except Busy:
            status = "pending_restore"
        except StorageError:
            if action == "upload":
                g["pending"] = True
            raise
        finally:
            g.update(status=status, checked=time.time())
            if head:
                g["head"] = {k: head.get(k) for k in ("device_name", "time", "size", "game")}
            self.save_state()
        if status in ("upload", "restore", "synced"):
            g.pop("conflict", None)
            self.save_state()
        return status

    def _upload(self, title: str, s: SaveDir, head: dict | None, etag: str) -> dict:
        files = read_tree(s.path / "0")
        h = files_hash(files)  # read again: the game may have written since the scan
        now = time.time()
        meta = self.meta(title, h, sum(map(len, files.values())))
        name = f"v-{utc_stamp(now)}-{self.device[:8]}-{h[:8]}.zip"
        folder = self.folder(title)
        self.storage.ensure_dir(folder)
        self.storage.put(f"{folder}/{name}", pack(files, meta))
        new = {"hash": h, "file": name, "device": self.device, "device_name": self.device_name, "time": now,
               "size": meta["size"], "game": meta["game"], "supersedes": (head or {}).get("supersedes", [])}
        self._write_head(title, new, etag, head is None)
        g = self.game(title)
        g.update(base=h, last_upload=now, pending=False)
        self._prune(title, keep=name)
        return new

    def _write_head(self, title: str, head: dict, etag: str, new: bool) -> None:
        path = f"{self.folder(title)}/head.json"
        data = json.dumps(head, ensure_ascii=False, indent=1).encode("utf-8")
        self.storage.put(path, data, if_match=etag or None, if_none_match=new)
        check, _ = self.head(title)  # storage without If-Match support: make sure our write is the one there
        if not check or check.get("hash") != head["hash"]:
            raise Precondition(path)

    def _prune(self, title: str, keep: str) -> None:
        files, _ = self.storage.entries(self.folder(title))
        versions = sorted(n for n in files if n.startswith("v-") and n.endswith(".zip"))
        for old in versions[:-KEEP_VERSIONS]:
            if old != keep:
                self.storage.delete(f"{self.folder(title)}/{old}")

    def _push_conflict(self, title: str, s: SaveDir, h: str) -> None:
        g = self.game(title)
        if g.get("conflict", {}).get("hash") == h:
            return  # already uploaded this version
        self.backup(s)
        files = read_tree(s.path / "0")
        h = files_hash(files)
        now = time.time()
        name = f"conflict-{utc_stamp(now)}-{self.device[:8]}-{h}.zip"
        folder = self.folder(title)
        self.storage.ensure_dir(folder)
        self.storage.put(f"{folder}/{name}", pack(files, self.meta(title, h, sum(map(len, files.values())))))
        g["conflict"] = {"hash": h, "file": name, "time": now}

    def _restore(self, title: str, s: SaveDir, head: dict) -> None:
        if self.busy():
            raise Busy(title)
        got = self.storage.get(f"{self.folder(title)}/{head['file']}")
        if got is None:
            raise StorageError(f"missing {head['file']}")
        files, _meta = unpack(got[0])
        h = files_hash(files)
        if h != head["hash"]:
            raise StorageError(f"{head['file']}: content does not match its hash")
        self.write_save(s, files)
        g = self.game(title)
        g.update(base=h, restored=time.time(), restored_from=head.get("device_name", ""))
        g.pop("scan", None)

    # --- writing a save --------------------------------------------------------
    def backup(self, s: SaveDir) -> Path:
        files = read_tree(s.path / "0")
        h = files_hash(files)
        folder = self.backup_dir / s.title
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{utc_stamp(time.time())}-{h[:8]}.zip"
        path.write_bytes(pack(files, self.meta(s.title, h, sum(map(len, files.values())))))
        for old in sorted(folder.glob("*.zip"))[:-KEEP_BACKUPS]:
            old.unlink()
        return path

    def write_save(self, s: SaveDir, files: dict[str, bytes]) -> None:
        """Replace 0/ and 1/ of one save folder with `files` (backup first; rolled back on any error).
        ExtraData stays as it is: it belongs to this install."""
        check = parse_extra((s.path / "ExtraData0").read_bytes())
        if check != (s.title, s.user, s.type):
            raise StorageError(f"{s.index}: not the save it was a moment ago")
        self.backup(s)
        staged = []
        try:
            for sub in ("0", "1"):
                new = s.path / f"{sub}.new"
                shutil.rmtree(new, ignore_errors=True)
                for rel, data in files.items():
                    p = new.joinpath(*rel.split("/"))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                new.mkdir(exist_ok=True)
                staged.append(sub)
            if self.busy():  # last check before touching the live folders
                raise Busy(s.title)
            swapped = []
            try:
                for sub in staged:
                    live, old, new = s.path / sub, s.path / f"{sub}.old", s.path / f"{sub}.new"
                    shutil.rmtree(old, ignore_errors=True)
                    if live.exists():
                        os.replace(live, old)
                    os.replace(new, live)
                    swapped.append(sub)
            except OSError:
                for sub in swapped + [x for x in staged if x not in swapped]:
                    live, old = s.path / sub, s.path / f"{sub}.old"
                    if old.exists():
                        shutil.rmtree(live, ignore_errors=True)
                        os.replace(old, live)
                raise
            for sub in staged:
                shutil.rmtree(s.path / f"{sub}.old", ignore_errors=True)
        finally:
            for sub in ("0", "1"):
                shutil.rmtree(s.path / f"{sub}.new", ignore_errors=True)

    # --- everything ------------------------------------------------------------
    def sync(self, allow_restore: bool = True, titles: list[str] | None = None, quick: bool = False) -> dict[str, str]:
        """Sync every local save (or `titles`); games only in the cloud are reported as needs_local.
        quick: just the local games' heads, fetched in parallel, no conflict listing — for a game about to start."""
        local = self.local()
        out = {}
        heads: dict[str, tuple] = {}
        if quick:
            from concurrent.futures import ThreadPoolExecutor
            titles = sorted(local)
            with ThreadPoolExecutor(max_workers=8) as pool:
                for title, got in zip(titles, pool.map(self.head, titles)):
                    heads[title] = got
        for title in titles or sorted(local):
            try:
                out[title] = self.sync_game(title, local.get(title), allow_restore=allow_restore,
                                            prefetched=heads.get(title))
                if titles is None:  # full check: also notice versions other devices left for a user to choose
                    mine = (self.game(title).get("conflict") or {}).get("file")
                    self.game(title)["remote_conflicts"] = len([c for c in self.conflicts(title) if c["file"] != mine])
            except StorageError as e:
                if str(e) == "auth" or str(e).startswith("network"):
                    raise
                self.game(title)["status"] = out[title] = f"error: {e}"
                self.save_state()
        if titles is None:
            try:
                for title in self.remote_titles():
                    if title not in local and title not in out:
                        self.game(title)["status"] = out[title] = "needs_local"
            except StorageError:
                pass
            self.save_state()
        return out

    def upload_if_changed(self, title: str) -> bool:
        """For uploads while a game runs (Deck): only if the save changed since it last matched the cloud."""
        s = self.local().get(title)
        g = self.game(title)
        if not s:
            return False
        if self.local_hash(s) == g.get("base"):
            return False
        return self.sync_game(title, s, allow_restore=False) == "upload"

    # --- choosing between versions (conflicts) and going back in history --------
    def conflicts(self, title: str) -> list[dict]:
        files, _ = self.storage.entries(self.folder(title))
        out = []
        for name in sorted(files):
            if name.startswith("conflict-") and name.endswith(".zip"):
                _, stamp, device, h = name[:-4].split("-", 3)
                out.append({"file": name, "time": stamp, "device": device, "hash": h})
        return out

    def versions(self, title: str) -> list[str]:
        files, _ = self.storage.entries(self.folder(title))
        return sorted((n for n in files if n.startswith("v-") and n.endswith(".zip")), reverse=True)

    def resolve(self, title: str, keep: str) -> None:
        """Settle a conflict for every device: keep "head" (the cloud's current version) or one conflict file.
        The other versions' hashes go into head["supersedes"]; devices holding them restore the kept one."""
        head, etag = self.head(title)
        conflicts = self.conflicts(title)
        if head is None:
            raise StorageError("no cloud version")
        dropped = [c["hash"] for c in conflicts if c["file"] != keep]
        folder = self.folder(title)
        if keep == "head":
            new = dict(head, supersedes=(head.get("supersedes", []) + dropped)[-KEEP_SUPERSEDED:])
        else:
            chosen = next(c for c in conflicts if c["file"] == keep)
            got = self.storage.get(f"{folder}/{keep}")
            if got is None:
                raise StorageError(f"missing {keep}")
            files, meta = unpack(got[0])
            h = files_hash(files)
            now = time.time()
            name = f"v-{utc_stamp(now)}-{meta.get('device', 'x')[:8]}-{h[:8]}.zip"
            self.storage.put(f"{folder}/{name}", got[0])
            new = {"hash": h, "file": name, "device": meta.get("device", ""),
                   "device_name": meta.get("device_name", chosen["device"]), "time": now, "size": len(got[0]),
                   "game": meta.get("game", head.get("game", "")),
                   "supersedes": (head.get("supersedes", []) + dropped + [head["hash"]])[-KEEP_SUPERSEDED:]}
        self._write_head(title, new, etag, False)
        for c in conflicts:
            self.storage.delete(f"{folder}/{c['file']}")
        if keep != "head":
            self._prune(title, keep=new["file"])
        self.game(title).pop("conflict", None)
        self.game(title)["remote_conflicts"] = 0
        self.save_state()

    def restore_version(self, title: str, version: str | Path) -> None:
        """Put an older version back on this device: a cloud version file name, or a local backup zip.
        The next sync uploads it as the newest version, so the other devices follow."""
        s = self.local().get(title)
        if s is None:
            raise StorageError("this device has no save for the game yet")
        if isinstance(version, Path):
            blob = version.read_bytes()
        else:
            got = self.storage.get(f"{self.folder(title)}/{version}")
            if got is None:
                raise StorageError(f"missing {version}")
            blob = got[0]
        files, _ = unpack(blob)
        if self.busy():
            raise Busy(title)
        self.write_save(s, files)
        self.game(title).pop("scan", None)
        self.save_state()

    def backups(self, title: str) -> list[Path]:
        return sorted((self.backup_dir / title).glob("*.zip"), reverse=True)
