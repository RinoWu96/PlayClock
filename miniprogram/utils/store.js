// Reads PlayClock/summary.json from the user's own 坚果云 (WebDAV) with a plain GET.
// Mini programs can't send WebDAV PROPFIND, which is why the desktop app writes this one summary file.
const base64 = require('./base64')

const DEFAULT_URL = 'https://dav.jianguoyun.com/dav/'
const KEY = 'playclock_dav'
const CACHE = 'playclock_summary'

function account() {
  return wx.getStorageSync(KEY) || null
}

function saveAccount(user, pass, url) {
  wx.setStorageSync(KEY, { user: user.trim(), pass: pass, url: (url || DEFAULT_URL).trim() })
}

function clearAccount() {
  wx.removeStorageSync(KEY)
  wx.removeStorageSync(CACHE)
}

function request(acc, path) {
  const base = acc.url.replace(/\/+$/, '') + '/'
  return new Promise((resolve, reject) => {
    wx.request({
      url: base + path,
      method: 'GET',
      header: { Authorization: 'Basic ' + base64.encode(acc.user + ':' + acc.pass) },
      dataType: 'text',          // anything but 'json': we parse the text ourselves
      responseType: 'text',
      timeout: 20000,
      success(res) {
        if (res.statusCode === 401 || res.statusCode === 403) reject(new Error('账号或应用密码不正确'))
        else if (res.statusCode === 404) reject(new Error('坚果云里还没有 PlayClock 的汇总数据：请先在电脑上的 PlayClock 开启云同步并同步一次'))
        else if (res.statusCode !== 200) reject(new Error('坚果云返回错误 ' + res.statusCode))
        else resolve(res.data)
      },
      fail(err) {
        const msg = (err && err.errMsg) || ''
        reject(new Error(msg.indexOf('url not in domain list') >= 0
          ? '请在小程序后台把 https://dav.jianguoyun.com 加入 request 合法域名'
          : '网络连接失败：' + msg))
      }
    })
  })
}

// Resolves to the summary object; also cached so the app opens instantly next time.
function loadSummary(acc) {
  acc = acc || account()
  if (!acc) return Promise.reject(new Error('NO_ACCOUNT'))
  return request(acc, 'PlayClock/summary.json').then(text => {
    const data = typeof text === 'string' ? JSON.parse(text) : text
    if (!data || data.format !== 1) throw new Error('汇总数据的格式不认识，请把电脑上的 PlayClock 更新到最新版')
    wx.setStorageSync(CACHE, data)
    return data
  })
}

function cachedSummary() {
  return wx.getStorageSync(CACHE) || null
}

module.exports = { account, saveAccount, clearAccount, loadSummary, cachedSummary, DEFAULT_URL }
