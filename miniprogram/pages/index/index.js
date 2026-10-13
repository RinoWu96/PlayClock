const fmt = require('../../utils/format')
const covers = require('../../utils/covers')
const app = getApp()

const STALE = 6 * 60   // "playing now" older than this is shown as possibly ended (desktop uploads every 2 min)

Page({
  data: { state: 'loading', error: '', loading: false },

  onShow() {
    this.unwatch = app.watch(g => this.render(g))
    app.refresh()
    this.timer = setInterval(() => { app.refresh(); this.render(app.globalData) }, 60 * 1000)
  },

  onHide() { this.stop() },
  onUnload() { this.stop() },

  stop() {
    if (this.unwatch) this.unwatch()
    clearInterval(this.timer)
  },

  onPullDownRefresh() {
    app.refresh().then(() => wx.stopPullDownRefresh())
  },

  render(g) {
    if (g.error === 'NO_ACCOUNT') return this.setData({ state: 'setup' })
    const s = g.summary
    if (!s) return this.setData({ state: g.loading ? 'loading' : 'error', error: g.error, loading: g.loading })
    const now = Date.now() / 1000
    const p = fmt.periods(s.daily)
    const cover = covers.byGame(s)
    const playing = s.playing.map(x => {
      const age = now - x.updated
      return {
        game: x.game, platform: x.platform, device: x.device, avatar: fmt.avatar(x.game), cover: cover[x.game] || '',
        clock: fmt.clock(x.seconds), stale: age > STALE, updated: fmt.ago(x.updated)
      }
    })
    const bars = fmt.bars(s.daily, 14)
    const total14 = bars.reduce((a, b) => a + b.seconds, 0)
    const platformsTotal = s.platforms.reduce((a, x) => a + x[1], 0) || 1
    const colors = ['#a46dff', '#3ddc97', '#ffb547', '#4cc9f0', '#ff6b9a', '#6d8dff']
    this.setData({
      state: 'ok', loading: g.loading, error: g.error,
      greeting: greeting(),
      playing,
      tiles: [
        { label: '今天', value: fmt.dur(p.today), color: '#6d8dff' },
        { label: '本周', value: fmt.dur(p.week), color: '#a46dff' },
        { label: '本月', value: fmt.dur(p.month), color: '#3ddc97' },
        { label: '累计', value: fmt.dur(s.total), color: '#ffb547', sub: `${s.games.length} 款游戏` }
      ],
      bars, total14: fmt.dur(total14), avg14: fmt.dur(total14 / 14),
      recent: s.recent.slice(0, 8).map(r => ({
        key: r.start + r.game,
        game: r.game, platform: r.platform, when: fmt.date(r.start, true), dur: fmt.dur(r.seconds), avatar: fmt.avatar(r.game),
        cover: cover[r.game] || ''
      })),
      platforms: s.platforms.map(([name, sec], i) => ({
        name, dur: fmt.dur(sec, true), pct: Math.max(2, Math.round(sec / platformsTotal * 100)),
        share: Math.round(sec / platformsTotal * 100), color: colors[i % colors.length]
      })),
      hasImports: s.has_imports,
      updated: fmt.ago(s.generated)
    })
  },

  openGame(e) {
    wx.navigateTo({ url: '/pages/game/game?name=' + encodeURIComponent(e.currentTarget.dataset.name) })
  },

  goSetup() { wx.switchTab({ url: '/pages/settings/settings' }) },
  openGuide() { wx.navigateTo({ url: '/pages/guide/guide' }) }
})

function greeting() {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 13 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}
