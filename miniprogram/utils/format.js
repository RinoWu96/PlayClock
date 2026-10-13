// Formatting and day maths shared by the pages (mirrors playclock/i18n.py, Chinese only).

function dur(sec, short) {
  sec = Math.floor(sec || 0)
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
  if (short) return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
  if (h) return m ? `${h} 小时 ${m} 分` : `${h} 小时`
  if (m) return `${m} 分钟`
  return sec ? `${sec} 秒` : '0 分钟'
}

function clock(sec) {
  sec = Math.floor(sec || 0)
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function pad(n) { return String(n).padStart(2, '0') }

function dayKey(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }

function date(ts, withTime) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return withTime ? `${dayKey(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}` : dayKey(d)
}

function ago(ts) {
  if (!ts) return '—'
  const d = Date.now() / 1000 - ts
  if (d < 90) return '刚刚'
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const then = new Date(ts * 1000); then.setHours(0, 0, 0, 0)
  const days = Math.round((today - then) / 86400000)
  if (days <= 0) return `${Math.floor(d / 3600)} 小时前`
  if (days === 1) return '昨天'
  if (days < 365) return `${days} 天前`
  return date(ts)
}

// today / this week (Mon) / this month, counted on the phone's own calendar from summary.daily
function periods(daily) {
  const map = {}
  daily.forEach(([d, s]) => { map[d] = s })
  const now = new Date(); now.setHours(0, 0, 0, 0)
  const sumFrom = start => {
    let total = 0
    for (let t = new Date(start); t <= now; t.setDate(t.getDate() + 1)) total += map[dayKey(t)] || 0
    return total
  }
  const week = new Date(now); week.setDate(now.getDate() - ((now.getDay() + 6) % 7))
  const month = new Date(now.getFullYear(), now.getMonth(), 1)
  return { today: map[dayKey(now)] || 0, week: sumFrom(week), month: sumFrom(month) }
}

// last n days as chart bars (zero-filled, phone calendar)
function bars(daily, n) {
  const map = {}
  daily.forEach(([d, s]) => { map[d] = s })
  const out = []
  const now = new Date(); now.setHours(0, 0, 0, 0)
  for (let i = n - 1; i >= 0; i--) {
    const t = new Date(now); t.setDate(now.getDate() - i)
    out.push({ key: dayKey(t), label: `${t.getMonth() + 1}/${t.getDate()}`, seconds: map[dayKey(t)] || 0 })
  }
  const peak = Math.max(1, ...out.map(b => b.seconds))
  out.forEach((b, i) => {
    b.pct = Math.round((b.seconds / peak) * 100)
    b.showLabel = i === 0 || i === n - 1 || i % 4 === (n - 1) % 4
    b.text = dur(b.seconds, true)
  })
  return out
}

// same idea as the desktop's generated covers: a stable colour per name + initials
function hue(name) {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return h % 360
}

function avatar(name) {
  const h = hue(name)
  const cjk = /[぀-鿿가-힯]/.test(name.slice(0, 2))
  const words = name.replace(/[:\-]/g, ' ').split(/\s+/).filter(w => /^[0-9A-Za-z]/.test(w))
  const initials = cjk ? name.slice(0, 2) : (words.slice(0, 2).map(w => w[0]).join('').toUpperCase() || name.slice(0, 2))
  return {
    initials,
    style: `background: linear-gradient(135deg, hsl(${h},55%,48%), hsl(${(h + 50) % 360},62%,28%));`
  }
}

module.exports = { dur, clock, date, ago, periods, bars, avatar }
