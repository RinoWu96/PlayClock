const fmt = require('../../utils/format')
const covers = require('../../utils/covers')
const app = getApp()

Page({
  data: { state: 'loading', chips: [], platform: '', query: '', games: [], subtitle: '' },

  onShow() {
    this.unwatch = app.watch(g => this.render(g))
    if (!app.globalData.summary) app.refresh()
  },
  onHide() { if (this.unwatch) this.unwatch() },
  onUnload() { if (this.unwatch) this.unwatch() },

  onPullDownRefresh() { app.refresh().then(() => wx.stopPullDownRefresh()) },

  render(g) {
    if (g.error === 'NO_ACCOUNT') return this.setData({ state: 'setup' })
    if (!g.summary) return this.setData({ state: g.loading ? 'loading' : 'error' })
    this.summary = g.summary
    this.filter()
  },

  filter() {
    const all = this.summary.games
    const counts = {}
    all.forEach(x => x.platforms.forEach(p => { counts[p] = (counts[p] || 0) + 1 }))
    const platform = counts[this.data.platform] ? this.data.platform : ''
    const chips = [{ key: '', text: `全部 ${all.length}` }].concat(
      Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(p => ({ key: p, text: `${p} ${counts[p]}` })))
    const q = this.data.query.trim().toLowerCase()
    const shown = all.filter(x => (!platform || x.platforms.indexOf(platform) >= 0) &&
                                  (!q || x.name.toLowerCase().indexOf(q) >= 0))
    const total = shown.reduce((a, x) => a + x.total, 0)
    this.setData({
      state: 'ok', chips, platform,
      subtitle: `${shown.length} 款游戏 · 合计 ${fmt.dur(total)}`,
      games: shown.slice(0, 300).map(x => ({
        name: x.name, platforms: x.platforms.join(' · '), total: fmt.dur(x.total), last: fmt.ago(x.last),
        avatar: fmt.avatar(x.name), cover: covers.path(x.cover)
      }))
    })
  },

  pick(e) { this.setData({ platform: e.currentTarget.dataset.key }, () => this.filter()) },
  search(e) { this.setData({ query: e.detail.value }, () => this.filter()) },
  openGame(e) { wx.navigateTo({ url: '/pages/game/game?name=' + encodeURIComponent(e.currentTarget.dataset.name) }) },
  goSetup() { wx.switchTab({ url: '/pages/settings/settings' }) }
})
