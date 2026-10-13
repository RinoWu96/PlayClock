const fmt = require('../../utils/format')
const app = getApp()

Page({
  data: { found: false },

  onLoad(q) {
    this.name = decodeURIComponent(q.name || '')
    wx.setNavigationBarTitle({ title: this.name })
    this.unwatch = app.watch(g => this.render(g))
  },
  onUnload() { if (this.unwatch) this.unwatch() },

  render(g) {
    const s = g.summary
    const game = s && s.games.find(x => x.name === this.name)
    if (!game) return this.setData({ found: false, name: this.name })
    const playing = s.playing.find(x => x.game === this.name)
    const sessions = s.recent.filter(r => r.game === this.name).map(r => ({
      key: r.start, when: fmt.date(r.start, true), platform: r.platform, dur: fmt.dur(r.seconds)
    }))
    this.setData({
      found: true, name: this.name, avatar: fmt.avatar(this.name),
      platforms: game.platforms.join(' / '),
      playing: playing ? fmt.clock(playing.seconds) : '',
      total: fmt.dur(game.total),
      stats: [
        { label: '游玩次数', value: `${game.sessions} 次` },
        { label: '平均每次', value: fmt.dur(game.total / Math.max(1, game.sessions)) },
        { label: '最近游玩', value: fmt.date(game.last), sub: fmt.ago(game.last) }
      ],
      sessions
    })
  }
})
