"""Multi-device sync through any WebDAV storage (坚果云 by default) — no server of our own.

Layout on the WebDAV side:
    PlayClock/devices/<device id>.json.gz    one file per device, written only by that device

Each file holds what that device knows it authored: its own sessions, plus every alias (rename/merge)
and tombstone (deletion) it has seen. Merging is last-writer-wins on `updated`, so the order in which
devices sync doesn't matter and no file is ever written by two devices. Network I/O runs in a worker
thread; the database is only touched on the GUI thread (export before, merge after).
"""
import base64
import ctypes
import gzip
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from ctypes import wintypes as wt

from .db import DB

FORMAT = 1
JIANGUOYUN = "https://dav.jianguoyun.com/dav/"
JIANGUOYUN_SITE = "https://www.jianguoyun.com/"
JIANGUOYUN_SECURITY = "https://www.jianguoyun.com/#/safety"
FOLDER = "PlayClock"
SESSION_FIELDS = ("uid", "game", "emulator", "start", "end", "seconds", "source", "device", "updated")


# --------------------------------------------------------------------------- payload


def export_payload(db: DB, device_name: str) -> dict:
    sessions = [dict(zip(SESSION_FIELDS, r)) for r in db.con.execute(
        f"SELECT {', '.join(SESSION_FIELDS)} FROM sessions WHERE device=?", (db.device,))]
    return {
        "format": FORMAT,
        "device": db.device,
        "name": device_name,
        "sessions": sessions,
        "aliases": [dict(r) for r in db.con.execute("SELECT raw, name, updated FROM aliases")],
        "tombstones": [dict(r) for r in db.con.execute("SELECT uid, at FROM tombstones")],
        "keys": [dict(r) for r in db.con.execute("SELECT key, game, updated FROM game_keys")],
    }


def encode(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)


def decode(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


def digest(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def merge_payload(db: DB, p: dict) -> int:
    """Fold another device's file into the local database. Returns how many rows changed."""
    if p.get("format") != FORMAT or p.get("device") == db.device:
        return 0
    con = db.con
    changed = 0
    with con:
        for a in p.get("aliases", []):  # renames first, so incoming sessions land under the new name
            row = con.execute("SELECT updated FROM aliases WHERE raw=?", (a["raw"],)).fetchone()
            if row is None or a["updated"] > row[0]:
                con.execute("INSERT OR REPLACE INTO aliases(raw, name, updated) VALUES(?, ?, ?)",
                            (a["raw"], a["name"], a["updated"]))
                con.execute("DELETE FROM aliases WHERE raw=? AND name=?", (a["name"], a["raw"]))  # no A<->B loop
                changed += con.execute("UPDATE sessions SET game=? WHERE game=?", (a["name"], a["raw"])).rowcount
        for k in p.get("keys", []):  # one name per game id: the newest choice wins on every device
            row = con.execute("SELECT game, updated FROM game_keys WHERE key=?", (k["key"],)).fetchone()
            if row is None:
                con.execute("INSERT INTO game_keys(key, game, updated) VALUES(?, ?, ?)",
                            (k["key"], k["game"], k["updated"]))
                changed += 1
            elif row["game"] != k["game"]:
                if (k["updated"], k["game"]) > (row["updated"], row["game"]):
                    db.rename_game(row["game"], k["game"])
                    con.execute("UPDATE game_keys SET game=?, updated=? WHERE key=?",
                                (k["game"], k["updated"], k["key"]))
                else:
                    db.rename_game(k["game"], row["game"])  # their name becomes an alias of ours
                changed += 1
        for t in p.get("tombstones", []):
            con.execute("INSERT OR IGNORE INTO tombstones(uid, at) VALUES(?, ?)", (t["uid"], t["at"]))
            changed += con.execute("DELETE FROM sessions WHERE uid=?", (t["uid"],)).rowcount
        dead = {r[0] for r in con.execute("SELECT uid FROM tombstones")}
        for s in p.get("sessions", []):
            if s["uid"] in dead:
                continue
            row = con.execute("SELECT updated FROM sessions WHERE uid=?", (s["uid"],)).fetchone()
            if row is not None and row[0] >= s["updated"]:
                continue
            s = dict(s, game=db.resolve(s["game"]))
            con.execute(
                f"INSERT INTO sessions({', '.join(SESSION_FIELDS)}) VALUES({', '.join('?' * len(SESSION_FIELDS))}) "
                "ON CONFLICT(uid) DO UPDATE SET " + ", ".join(f"{f}=excluded.{f}" for f in SESSION_FIELDS[1:]),
                tuple(s[f] for f in SESSION_FIELDS))
            changed += 1
    return changed


# --------------------------------------------------------------------------- WebDAV


class SyncError(Exception):
    pass


class WebDAV:
    """Minimal blocking WebDAV client (PROPFIND / MKCOL / GET / PUT) on the standard library."""

    def __init__(self, url: str, user: str, password: str, timeout: float = 30):
        self.base = url.rstrip("/") + "/"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return self.base + "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)

    def request(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(self._url(path), data=data, method=method,
                                     headers={"Authorization": self.auth, "User-Agent": "PlayClock", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SyncError("auth") from e
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise SyncError(f"network: {e}") from e

    def check(self):
        status, _ = self.request("PROPFIND", "", headers={"Depth": "0"})
        if status not in (200, 207):
            raise SyncError(f"HTTP {status}")

    def ensure_dir(self, path: str):
        status, _ = self.request("MKCOL", path)
        if status not in (200, 201, 405):  # 405 = already exists
            raise SyncError(f"MKCOL {path}: HTTP {status}")

    def listing(self, path: str) -> dict[str, str]:
        """File name -> version tag (etag, else last-modified + size) for files directly in `path`."""
        status, body = self.request("PROPFIND", path + "/", headers={"Depth": "1"})
        if status == 404:
            return {}
        if status not in (200, 207):
            raise SyncError(f"PROPFIND {path}: HTTP {status}")
        ns = {"d": "DAV:"}
        out = {}
        for resp in ET.fromstring(body).findall("d:response", ns):
            href = urllib.parse.unquote(resp.findtext("d:href", "", ns)).rstrip("/")
            prop = resp.find(".//d:prop", ns)
            if prop is None or prop.find("d:resourcetype/d:collection", ns) is not None:
                continue
            tag = (prop.findtext("d:getetag", "", ns) or
                   prop.findtext("d:getlastmodified", "", ns) + prop.findtext("d:getcontentlength", "", ns))
            out[href.rsplit("/", 1)[-1]] = tag
        return out

    def get(self, path: str) -> bytes:
        status, body = self.request("GET", path)
        if status != 200:
            raise SyncError(f"GET {path}: HTTP {status}")
        return body

    def put(self, path: str, data: bytes):
        status, _ = self.request("PUT", path, data=data, headers={"Content-Type": "application/octet-stream"})
        if status not in (200, 201, 204):
            raise SyncError(f"PUT {path}: HTTP {status}")


def run_sync(client: WebDAV, device: str, upload: bytes | None, known: dict[str, str],
             extra: dict[str, bytes] | None = None, thumbs: dict | None = None):
    """One round of network work (worker thread). Returns (downloaded blobs by name, new version tags).
    `extra` = other files to write in the PlayClock folder (summary.json for the mini program).
    `thumbs` = {"files": {remote name: local cover path}, "make": fn(path) -> jpeg bytes, "batch": n}:
    cover thumbnails the phone shows; only ones missing remotely are made and uploaded, n per round."""
    client.ensure_dir(FOLDER)
    client.ensure_dir(f"{FOLDER}/devices")
    own = f"{device}.json.gz"
    if upload is not None:
        client.put(f"{FOLDER}/devices/{own}", upload)
    for name, data in (extra or {}).items():
        client.put(f"{FOLDER}/{name}", data)
    if thumbs and thumbs["files"]:
        client.ensure_dir(f"{FOLDER}/covers")
        have = client.listing(f"{FOLDER}/covers")
        todo = [n for n in thumbs["files"] if n not in have][:thumbs.get("batch", 60)]
        for name in todo:
            data = thumbs["make"](thumbs["files"][name])
            if data:
                client.put(f"{FOLDER}/covers/{name}", data)
    tags = client.listing(f"{FOLDER}/devices")
    blobs = {name: client.get(f"{FOLDER}/devices/{name}")
             for name, tag in tags.items() if name != own and name.endswith(".json.gz") and known.get(name) != tag}
    return blobs, tags


def live_devices(names: dict[str, str], tags: dict[str, str]) -> dict[str, str]:
    """Known device names, keeping only devices whose file is still in the remote folder."""
    return {d: n for d, n in names.items() if f"{d}.json.gz" in tags}


# --------------------------------------------------------------------------- secrets


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.WinDLL("crypt32"), ctypes.WinDLL("kernel32")
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), wt.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wt.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wt.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    args = [ctypes.byref(src), None if not protect else "PlayClock", None, None, None, 0, ctypes.byref(out)]
    if not fn(*args):
        raise SyncError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(secret: str) -> str:
    """Encrypt for the current Windows user (DPAPI) so config.json never holds the password in clear."""
    return base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode()


def unprotect(token: str) -> str:
    return _dpapi(base64.b64decode(token), False).decode("utf-8")
