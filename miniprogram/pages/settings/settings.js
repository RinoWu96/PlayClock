const store = require('../../utils/store')
const app = getApp()

Page({
  data: { connected: false, user: '', pass: '', url: store.DEFAULT_URL, status: '', error: false, busy: false },

  onShow() {
    const acc = store.account()
    this.setData(acc ? { connected: true, user: acc.user, url: acc.url, status: '' }
                     : { connected: false, status: '' })
  },

  input(e) { this.setData({ [e.currentTarget.dataset.field]: e.detail.value }) },

  save() {
    const { user, pass, url } = this.data
    if (!user.trim() || !pass) return this.setData({ status: '请填写坚果云账号和应用密码', error: true })
    this.setData({ busy: true, status: '正在连接…', error: false })
    const acc = { user: user.trim(), pass, url: (url || store.DEFAULT_URL).trim() }
    store.loadSummary(acc)
      .then(() => {
        store.saveAccount(acc.user, acc.pass, acc.url)
        this.setData({ connected: true, pass: '', status: '连接成功', error: false })
        app.refresh()
        wx.switchTab({ url: '/pages/index/index' })
      })
      .catch(e => this.setData({ status: e.message, error: true }))
      .then(() => this.setData({ busy: false }))
  },

  openGuide() { wx.navigateTo({ url: '/pages/guide/guide' }) },

  logout() {
    wx.showModal({
      title: '断开坚果云',
      content: '会清除手机上保存的账号和缓存数据，电脑和坚果云上的记录不受影响。',
      success: r => {
        if (!r.confirm) return
        store.clearAccount()
        app.globalData.summary = null
        app.globalData.error = 'NO_ACCOUNT'
        app.emit()
        this.setData({ connected: false, user: '', pass: '', status: '' })
      }
    })
  }
})
