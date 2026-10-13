"""Run the mini program's JavaScript in QuickJS with a stub `wx` runtime.

Checks the pure helpers (base64, formatting, day maths) and renders every page against a real
summary built by playclock.summary, so mistakes surface without WeChat DevTools.
"""
import base64
import json
import time
from pathlib import Path

import pytest

quickjs = pytest.importorskip("quickjs")

MP = Path(__file__).resolve().parent.parent / "miniprogram"

RUNTIME = r"""
var __modules = {}, __cache = {}, __pages = {}, __app = null, __storage = {}, __requests = [];
function __resolve(from, path) {
  var parts = from.split('/'); parts.pop();
  path.split('/').forEach(function (p) { if (p === '..') parts.pop(); else if (p !== '.') parts.push(p); });
  var r = parts.join('/'); return r.endsWith('.js') ? r : r + '.js';
}
function __require(from) {
  return function (path) {
    var id = __resolve(from, path);
    if (!__cache[id]) { var m = { exports: {} }; __cache[id] = m; __modules[id](m, m.exports, __require(id)); }
    return __cache[id].exports;
  };
}
var wx = {
  getStorageSync: function (k) { return __storage[k] === undefined ? '' : __storage[k]; },
  setStorageSync: function (k, v) { __storage[k] = v; },
  removeStorageSync: function (k) { delete __storage[k]; },
  request: function (o) { __requests.push(o); },
  downloadFile: function (o) { __downloads.push(o); },     // tests answer them with __answer()
  env: { USER_DATA_PATH: 'wxfile://usr' },
  getFileSystemManager: function () {
    return {
      accessSync: function (p) { if (!__files[p]) throw new Error('no such file ' + p); },
      mkdirSync: function (p) { __files[p] = 'dir'; },
      unlinkSync: function (p) { if (!__files[p]) throw new Error('no such file'); delete __files[p]; }
    };
  },
  setNavigationBarTitle: function () {}, stopPullDownRefresh: function () {},
  navigateTo: function (o) { __nav.push(o.url); }, switchTab: function (o) { __nav.push(o.url); }, showModal: function () {},
  setClipboardData: function (o) { __clipboard = o.data; if (o.success) o.success(); }, showToast: function () {}
};
var __downloads = [], __files = {}, __timers = [], __nav = [], __clipboard = null;
// answer a pending download (the oldest, or the one for `name`) with an HTTP status (200 writes the file)
function __answer(status, name) {
  var i = name ? __downloads.findIndex(function (d) { return d.url.endsWith("/" + name); }) : 0;
  var o = __downloads.splice(i, 1)[0];
  if (status === 200) __files[o.filePath] = true;
  if (status) o.success({ statusCode: status }); else o.fail({ errMsg: 'downloadFile:fail' });
  return o;
}
function App(cfg) { __app = cfg; __app.globalData = JSON.parse(JSON.stringify(cfg.globalData)); }
function getApp() { return __app; }
function Page(cfg) { __pages[__current] = cfg; }
function setInterval() { return 1; } function clearInterval() {}
function setTimeout(fn) { __timers.push(fn); return __timers.length; }
function __flushTimers() { var t = __timers; __timers = []; t.forEach(function (f) { f(); }); }
var __current = '';
function __page(id) {
  var p = Object.assign({}, __pages[id]);
  p.data = JSON.parse(JSON.stringify(p.data || {}));
  p.setData = function (d, cb) { Object.assign(this.data, d); if (cb) cb(); };
  return p;
}
"""


def load(ctx, rel):
    src = (MP / rel).read_text(encoding="utf-8")
    ctx.eval(f"__modules[{json.dumps(rel)}] = function (module, exports, require) {{\n{src}\n}};")


@pytest.fixture
def js():
    ctx = quickjs.Context()
    ctx.eval(RUNTIME)
    for f in ("utils/base64.js", "utils/format.js", "utils/covers.js", "utils/store.js"):
        load(ctx, f)
    load(ctx, "app.js")
    ctx.eval('__current="app.js"; __require("app.js")("./app.js"); __app.onLaunch()')
    for page in ("index", "library", "game", "settings", "guide"):
        rel = f"pages/{page}/{page}.js"
        load(ctx, rel)
        ctx.eval(f'__current="{page}"; __require({json.dumps(rel)})("./{page}.js")')
    return ctx


def test_base64_matches_python(js):
    for s in ("me@example.com:app-pass", "账号:密码 🎮", "a", "ab"):
        got = js.eval(f'__require("x.js")("./utils/base64.js").encode({json.dumps(s)})')
        assert got == base64.b64encode(s.encode()).decode()


def test_period_maths(js):
    today = time.strftime("%Y-%m-%d")
    daily = [[time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400)), 3600] for i in range(60)][::-1]
    p = json.loads(js.eval(f'JSON.stringify(__require("x.js")("./utils/format.js").periods({json.dumps(daily)}))'))
    lt = time.localtime()
    assert p["today"] == 3600 and p["week"] == 3600 * (lt.tm_wday + 1) and p["month"] == 3600 * lt.tm_mday
    bars = json.loads(js.eval(f'JSON.stringify(__require("x.js")("./utils/format.js").bars({json.dumps(daily)}, 14))'))
    assert len(bars) == 14 and bars[-1]["key"] == today and all(b["pct"] == 100 for b in bars)
    assert js.eval('__require("x.js")("./utils/format.js").dur(3*3600+5*60)') == "3 小时 5 分"


def summary_fixture(tmp_path):
    from playclock.db import DB
    from playclock.importers import EmuGame, apply_import, plan_import
    from playclock.summary import build_summary
    db = DB(tmp_path / "s.db", device="PC")
    now = time.time()
    sid = db.start_session("塞尔达传说 王国之泪", "Ryujinx", now - 2700)
    db.update_session(sid, now - 20, 2680)
    for d in range(1, 9):
        s = db.start_session("Super Mario Odyssey", "Ryujinx", now - d * 86400)
        db.update_session(s, now - d * 86400 + 1800, 1800)
    apply_import(db, plan_import(db, [EmuGame("Warframe 星际战甲", 4605660, now - 9e6)], "Steam"), "Steam", "steam:1")
    return build_summary(db, lambda e: {"Ryujinx": "Switch"}.get(e, e), {"PC": "RHINO"}, now,
                         covers={"Super Mario Odyssey": "m1.png", "Warframe 星际战甲": "w1.png"})


def test_pages_render_a_real_summary(js, tmp_path):
    summary = summary_fixture(tmp_path)
    js.eval(f"__app.globalData.summary = {json.dumps(summary, ensure_ascii=False)};")
    out = json.loads(js.eval("""
      var r = {};
      var idx = __page('index'); idx.render(__app.globalData); r.index = idx.data;
      var lib = __page('library'); lib.render(__app.globalData); r.library = lib.data;
      lib.data.platform = 'Switch'; lib.filter(); r.libSwitch = lib.data.games.map(function (g) { return g.name; });
      var g = __page('game'); g.name = 'Super Mario Odyssey'; g.render(__app.globalData); r.game = g.data;
      JSON.stringify(r);
    """))
    idx = out["index"]
    assert idx["state"] == "ok"
    assert [p["game"] for p in idx["playing"]] == ["塞尔达传说 王国之泪"] and not idx["playing"][0]["stale"]
    assert idx["playing"][0]["device"] == "RHINO" and idx["playing"][0]["platform"] == "Switch"
    assert idx["tiles"][3]["value"] == "1284 小时 5 分"      # all-time: Steam import 1279h21m + Switch 4h44m
    assert len(idx["bars"]) == 14 and len(idx["recent"]) == 8
    assert [p["name"] for p in idx["platforms"]] == ["Steam", "Switch"]
    assert out["library"]["chips"][0]["text"] == "全部 3"
    assert sorted(out["libSwitch"]) == ["Super Mario Odyssey", "塞尔达传说 王国之泪"]
    assert out["game"]["found"] and out["game"]["stats"][0]["value"] == "8 次"


def test_setup_state_without_account(js):
    out = json.loads(js.eval("""
      __app.globalData.error = 'NO_ACCOUNT';
      var idx = __page('index'); idx.render(__app.globalData); JSON.stringify(idx.data.state);
    """))
    assert out == "setup"


def test_request_sends_basic_auth_to_summary_file(js):
    js.eval("""
      var store = __require('x.js')('./utils/store.js');
      store.loadSummary({ user: 'me@example.com', pass: 'app-pass', url: 'https://dav.jianguoyun.com/dav/' });
    """)
    req = json.loads(js.eval("JSON.stringify(__requests[0])"))
    assert req["url"] == "https://dav.jianguoyun.com/dav/PlayClock/summary.json" and req["method"] == "GET"
    assert req["header"]["Authorization"] == "Basic " + base64.b64encode(b"me@example.com:app-pass").decode()


def drain(js):
    while js.execute_pending_job():
        pass


def test_covers_download_once_with_auth_and_show_up(js, tmp_path):
    summary = summary_fixture(tmp_path)
    js.eval(f"""
      __storage.playclock_dav = {{ user: 'me@example.com', pass: 'app-pass', url: 'https://dav.jianguoyun.com/dav' }};
      var S = {json.dumps(summary, ensure_ascii=False)};
      var covers = __require('x.js')('./utils/covers.js');
      var renders = 0; __app.watch(function () {{ renders++; }});
      covers.sync(S, __storage.playclock_dav);
    """)
    reqs = json.loads(js.eval("JSON.stringify(__downloads)"))
    assert len(reqs) == 2
    assert {r["url"] for r in reqs} == {"https://dav.jianguoyun.com/dav/PlayClock/covers/m1.jpg",
                                        "https://dav.jianguoyun.com/dav/PlayClock/covers/w1.jpg"}
    assert reqs[0]["header"]["Authorization"] == "Basic " + base64.b64encode(b"me@example.com:app-pass").decode()
    assert reqs[0]["filePath"].startswith("wxfile://usr/covers/")

    js.eval("__answer(200, 'm1.jpg')")       # m1 arrives
    drain(js)
    js.eval("__answer(404, 'w1.jpg')")       # w1 not uploaded by the desktop yet
    drain(js)
    js.eval("__flushTimers()")
    assert js.eval("renders") == 2                                   # initial watch + one batched re-render
    out = json.loads(js.eval("""
      var idx = __page('index'); __app.globalData.summary = S; idx.render(__app.globalData);
      var lib = __page('library'); lib.render(__app.globalData);
      var g = __page('game'); g.name = 'Super Mario Odyssey'; g.render(__app.globalData);
      JSON.stringify({ recent: idx.data.recent.map(function (r) { return [r.game, r.cover]; }),
                       lib: lib.data.games.map(function (r) { return [r.name, r.cover]; }), game: g.data.cover });
    """))
    m = "wxfile://usr/covers/m1.jpg"
    assert out["game"] == m
    assert dict(out["lib"]) == {"Warframe 星际战甲": "", "Super Mario Odyssey": m, "塞尔达传说 王国之泪": ""}
    assert {c for g, c in out["recent"] if g == "Super Mario Odyssey"} == {m}

    js.eval("covers.sync(S, __storage.playclock_dav)")              # cached one isn't fetched again,
    assert js.eval("__downloads.length") == 0                        # the 404 waits for the retry window
    js.eval("__require('x.js')('./utils/store.js').clearAccount()")
    assert js.eval("Object.keys(__files).filter(function (f) { return f.endsWith('.jpg'); }).length") == 0
    assert js.eval("covers.path('covers/m1.jpg')") == ""


def test_concurrency_and_stale_cache_cleanup(js, tmp_path):
    js.eval("""
      var covers = __require('x.js')('./utils/covers.js');
      var acc = { user: 'u', pass: 'p', url: 'https://dav.jianguoyun.com/dav/' };
      var S = { games: [] };
      for (var i = 0; i < 10; i++) S.games.push({ name: 'g' + i, cover: 'covers/c' + i + '.jpg' });
      S.games.push({ name: 'none', cover: null });
      covers.sync(S, acc);
    """)
    assert js.eval("__downloads.length") == js.eval("covers.CONCURRENCY") == 4
    for _ in range(10):
        js.eval("__answer(200)")
        drain(js)
    assert js.eval("__downloads.length") == 0
    assert js.eval("covers.path('covers/c9.jpg')") == "wxfile://usr/covers/c9.jpg"
    js.eval("""
      delete __files['wxfile://usr/covers/c1.jpg'];              // cleared by WeChat behind our back
      S.games = S.games.slice(0, 2);                             // c2..c9 no longer used
      covers.sync(S, acc);
    """)
    assert js.eval("__downloads.length") == 1 and js.eval("__downloads[0].url").endswith("/c1.jpg")
    assert js.eval("__files['wxfile://usr/covers/c5.jpg']") is None


def test_guide_is_registered_linked_and_copies_the_download_link(js):
    app_json = json.loads((MP / "app.json").read_text(encoding="utf-8"))
    assert "pages/guide/guide" in app_json["pages"]
    out = json.loads(js.eval("""
      var g = __page('guide');
      g.copy({ currentTarget: { dataset: { text: g.data.download } } });
      var s = __page('settings'); s.openGuide();
      JSON.stringify({ clip: __clipboard, nav: __nav });
    """))
    assert out["clip"] == "https://github.com/RinoWu96/PlayClock/releases/latest"
    assert out["nav"] == ["/pages/guide/guide"]
    wxml = (MP / "pages/guide/guide.wxml").read_text(encoding="utf-8")
    assert "{{download}}" in wxml and "连接坚果云" in wxml
