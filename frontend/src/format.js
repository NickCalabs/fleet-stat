export function fmtNum(n) {
  if (n == null || isNaN(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e9) return (n / 1e9).toFixed(1) + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (abs >= 1e4) return (n / 1e3).toFixed(0) + 'k'
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return Math.round(n).toString()
}

export function fmtGB(bytes) {
  if (bytes == null) return '—'
  return (bytes / 1e9).toFixed(1) + ' GB'
}

export function fmtPct(v, digits = 0) {
  if (v == null || isNaN(v)) return '—'
  return v.toFixed(digits) + '%'
}

export function fmtAgo(ts) {
  if (!ts) return '—'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return Math.round(s) + 's ago'
  if (s < 3600) return Math.round(s / 60) + 'm ago'
  if (s < 86400) return (s / 3600).toFixed(1) + 'h ago'
  return (s / 86400).toFixed(1) + 'd ago'
}

export function fmtUptime(s) {
  if (s == null) return '—'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  if (d > 0) return `${d}d ${h}h`
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function fmtSecs(s) {
  if (s == null || isNaN(s)) return '—'
  if (s < 10) return s.toFixed(1) + 's'
  if (s < 90) return Math.round(s) + 's'
  return (s / 60).toFixed(1) + 'm'
}

export function fmtTick(ts, bucketS) {
  const d = new Date(ts * 1000)
  if (bucketS >= 86400) return `${d.getMonth() + 1}/${d.getDate()}`
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  if (bucketS >= 21600) return `${d.getMonth() + 1}/${d.getDate()} ${hh}:00`
  return `${hh}:${mm}`
}
