// How to set up PlayClock on the PC, so the mini program has something to show.
// Mini programs can't open outside web pages, so the download link is copied for the PC browser.
const DOWNLOAD = 'https://github.com/RinoWu96/PlayClock/releases/latest'
const JIANGUOYUN = 'https://www.jianguoyun.com'

Page({
  data: { download: DOWNLOAD, jianguoyun: JIANGUOYUN },

  copy(e) {
    const text = e.currentTarget.dataset.text
    wx.setClipboardData({
      data: text,
      success: () => wx.showToast({ title: '已复制，发到电脑上用浏览器打开', icon: 'none' })
    })
  },

  goSetup() { wx.switchTab({ url: '/pages/settings/settings' }) }
})
