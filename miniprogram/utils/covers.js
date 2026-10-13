// Cover thumbnails: PlayClock/covers/<id>.jpg on the user's 坚果云, downloaded once into the
// mini program's own file space. The desktop gives every new cover a fresh id, so a cached file
// never goes stale: a changed cover is simply a new name.
const base64 = require('./base64')

const CONCURRENCY = 4
const RETRY = 5 * 60 * 1000          // the desktop uploads a batch of thumbnails per sync, so missing ones come later
const KEY = 'playclock_covers'       // remote name -> true for files already saved locally

let saved = null                     // loaded lazily from storage
let failed = {}                      // name -> time of the last failed try; retried after RETRY
let queue = []
let queued = {}
let running = 0
let onChange = null
let changeTimer = null

function dir() { return wx.env.USER_DATA_PATH + '/covers' }
function localPath(name) { return dir() + '/' + name }

function index() {
  if (!saved) saved = wx.getStorageSync(KEY) || {}
  return saved
}

function fileName(cover) { return cover ? cover.split('/').pop() : '' }

// Local file path for a summary entry's "cover" field, or '' if it isn't downloaded (yet).
function path(cover) {
  const name = fileName(cover)
  return name && index()[name] ? localPath(name) : ''
}

// game name -> local thumbnail path ('' when there is none yet)
function byGame(summary) {
  const out = {}
  summary.games.forEach(g => { out[g.name] = path(g.cover) })
  return out
}

// Called whenever thumbnails arrive (batched), so pages can re-render.
function listen(fn) { onChange = fn }

function changed() {
  if (changeTimer || !onChange) return
  changeTimer = setTimeout(() => { changeTimer = null; onChange() }, 300)
}

function ensureDir() {
  const fs = wx.getFileSystemManager()
  try { fs.accessSync(dir()) } catch (e) { try { fs.mkdirSync(dir(), true) } catch (e2) { /* already there */ } }
  return fs
}

// Start downloading every cover in the summary that isn't cached; drop cached files no game uses any more.
function sync(summary, acc) {
  if (!summary || !acc) return
  const fs = ensureDir()
  const idx = index()
  const wanted = {}
  summary.games.forEach(g => { const n = fileName(g.cover); if (n) wanted[n] = true })
  // a cached entry whose file was cleared (e.g. by WeChat) is downloaded again
  Object.keys(idx).forEach(n => {
    let exists = true
    try { fs.accessSync(localPath(n)) } catch (e) { exists = false }
    if (!wanted[n] || !exists) {
      if (exists) { try { fs.unlinkSync(localPath(n)) } catch (e) { /* ignore */ } }
      delete idx[n]
    }
  })
  wx.setStorageSync(KEY, idx)
  const base = acc.url.replace(/\/+$/, '') + '/PlayClock/covers/'
  const auth = 'Basic ' + base64.encode(acc.user + ':' + acc.pass)
  Object.keys(wanted).forEach(n => {
    if (!idx[n] && !queued[n] && !(Date.now() - (failed[n] || 0) < RETRY)) { queued[n] = true; queue.push({ name: n, url: base + n, auth }) }
  })
  pump()
}

function pump() {
  while (running < CONCURRENCY && queue.length) {
    const job = queue.shift()
    running++
    download(job).then(() => { running--; delete queued[job.name]; pump() })
  }
}

function download(job) {
  return new Promise(resolve => {
    wx.downloadFile({
      url: job.url,
      header: { Authorization: job.auth },
      filePath: localPath(job.name),
      timeout: 30000,
      success(res) {
        if (res.statusCode === 200) {
          index()[job.name] = true
          wx.setStorageSync(KEY, index())
          changed()
        } else {
          failed[job.name] = Date.now()
          try { wx.getFileSystemManager().unlinkSync(localPath(job.name)) } catch (e) { /* error body, if any */ }
        }
        resolve()
      },
      fail() { failed[job.name] = Date.now(); resolve() }
    })
  })
}

// Forget everything (account removed).
function clear() {
  const fs = wx.getFileSystemManager()
  Object.keys(index()).forEach(n => { try { fs.unlinkSync(localPath(n)) } catch (e) { /* ignore */ } })
  saved = {}
  failed = {}
  wx.removeStorageSync(KEY)
}

module.exports = { path, byGame, sync, listen, clear, CONCURRENCY }
