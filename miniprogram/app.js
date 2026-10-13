const store = require('./utils/store')

App({
  globalData: { summary: null, error: '', loading: false, listeners: [] },

  onLaunch() {
    this.globalData.summary = store.cachedSummary()
  },

  // Fetch the latest summary.json; every page subscribed via watch() gets the new state.
  refresh() {
    const g = this.globalData
    if (g.loading) return g.pending
    g.loading = true
    this.emit()
    g.pending = store.loadSummary()
      .then(s => { g.summary = s; g.error = '' })
      .catch(e => { g.error = e.message === 'NO_ACCOUNT' ? 'NO_ACCOUNT' : e.message })
      .then(() => { g.loading = false; this.emit() })
    return g.pending
  },

  watch(fn) {
    this.globalData.listeners.push(fn)
    fn(this.globalData)
    return () => { this.globalData.listeners = this.globalData.listeners.filter(f => f !== fn) }
  },

  emit() {
    this.globalData.listeners.forEach(fn => fn(this.globalData))
  }
})
