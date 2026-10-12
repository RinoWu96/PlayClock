"""SteamGridDB client (cover art). Async via QNetworkAccessManager; callbacks run on the GUI thread.

API docs: https://www.steamgriddb.com/api/v2 — every call needs the user's own API key.
"""
import json
from urllib.parse import quote

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .i18n import tr

API = "https://www.steamgriddb.com/api/v2"
SITE_URL = "https://www.steamgriddb.com/"
KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"
# Landscape grids match PlayClock's 16:9-ish cards best.
GRID_QUERY = "dimensions=920x430,460x215&types=static&nsfw=false&humor=false"


class SteamGridDB(QObject):
    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key.strip()
        self.nam = QNetworkAccessManager(self)

    def _get(self, url: str, cb, raw: bool = False):
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", f"PlayClock/{__version__}".encode())
        if not raw:
            req.setRawHeader(b"Authorization", f"Bearer {self.key}".encode())
        req.setTransferTimeout(20000)
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._done(reply, cb, raw))

    @staticmethod
    def _done(reply: QNetworkReply, cb, raw: bool):
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        data = bytes(reply.readAll())
        err = reply.error()
        err_text = reply.errorString()
        reply.deleteLater()
        if status == 401 or status == 403:
            cb(None, tr("API Key 无效，请检查后重新填写。"))
        elif err != QNetworkReply.NoError and not (status and 200 <= status < 300):
            cb(None, tr("网络错误：{e}", e=err_text))
        elif raw:
            cb(data, None)
        else:
            try:
                body = json.loads(data)
            except ValueError:
                cb(None, tr("SteamGridDB 返回了无法识别的数据。"))
                return
            if not body.get("success", False):
                cb(None, "; ".join(body.get("errors", [])) or tr("SteamGridDB 返回了错误。"))
            else:
                cb(body.get("data") or [], None)

    def verify(self, cb):
        """cb(ok: bool, error: str | None)"""
        self.search("zelda", lambda data, err: cb(err is None, err))

    def search(self, term: str, cb):
        """cb([{id, name, year}], error)"""
        def done(data, err):
            if err:
                cb([], err)
                return
            out = []
            for g in data:
                year = ""
                if g.get("release_date"):
                    from datetime import datetime, timezone
                    year = str(datetime.fromtimestamp(g["release_date"], timezone.utc).year)
                out.append({"id": g["id"], "name": g.get("name", ""), "year": year})
            cb(out, None)
        self._get(f"{API}/search/autocomplete/{quote(term.strip(), safe='')}", done)

    def grids(self, game_id: int, cb):
        """cb([{url, thumb, width, height}], error)"""
        def done(data, err):
            cb([] if err else [{"url": g["url"], "thumb": g.get("thumb") or g["url"],
                                "width": g.get("width"), "height": g.get("height")} for g in data], err)
        self._get(f"{API}/grids/game/{game_id}?{GRID_QUERY}", done)

    def download(self, url: str, cb):
        """cb(bytes | None, error)"""
        self._get(url, cb, raw=True)

    def first_cover(self, term: str, cb):
        """Best guess for automatic covers: first search hit, its first grid. cb(bytes | None, error)"""
        def got_games(games, err):
            if err or not games:
                cb(None, err)
                return
            self.grids(games[0]["id"], got_grids)

        def got_grids(grids, err):
            if err or not grids:
                cb(None, err)
                return
            self.download(grids[0]["url"], cb)

        self.search(term, got_games)
