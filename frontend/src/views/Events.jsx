import React, { useEffect, useMemo, useState } from 'react'
import { STATUS, INK } from '../theme.js'

const LEVELS = [
  { id: '', label: 'all' },
  { id: 'warn', label: 'warn+' },
  { id: 'error', label: 'errors' },
]
const WINDOWS = [
  { label: '6 h', h: 6 },
  { label: '24 h', h: 24 },
  { label: '7 d', h: 168 },
]
const LEVEL_COLOR = { info: INK.muted, warn: STATUS.warning, error: STATUS.critical }
const KEEP = 1000

function fmtTime(ts) {
  const d = new Date(ts * 1000)
  const now = new Date()
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

// Initial page from /api/events, then live rows over SSE (/api/events/stream).
export default function Events() {
  const [level, setLevel] = useState('')
  const [hours, setHours] = useState(24)
  const [node, setNode] = useState('')
  const [rows, setRows] = useState([])
  const [live, setLive] = useState(false)
  const [open, setOpen] = useState(null)

  useEffect(() => {
    let es
    let alive = true
    const q = new URLSearchParams({ hours, limit: 500 })
    if (level) q.set('level', level)
    fetch(`/api/events?${q}`).then((r) => r.json()).then((j) => {
      if (!alive) return
      setRows(j.events)
      const sq = new URLSearchParams({ after: j.cursor })
      if (level) sq.set('level', level)
      es = new EventSource(`/api/events/stream?${sq}`)
      es.onopen = () => setLive(true)
      es.onerror = () => setLive(false)
      es.addEventListener('event', (m) => {
        const e = JSON.parse(m.data)
        setRows((prev) => (prev.some((p) => p.id === e.id) ? prev
          : [e, ...prev].sort((a, b) => b.ts - a.ts).slice(0, KEEP)))
      })
    }).catch(() => setLive(false))
    return () => { alive = false; if (es) es.close() }
  }, [level, hours])

  const nodes = useMemo(
    () => [...new Set(rows.map((r) => r.node).filter(Boolean))].sort(),
    [rows]
  )
  const shown = node ? rows.filter((r) => r.node === node) : rows

  return (
    <>
      <div className="filter-row">
        <span className="kv-label">level</span>
        {LEVELS.map((l) => (
          <button key={l.id} className={`pill ${level === l.id ? 'pill-on' : ''}`}
            onClick={() => setLevel(l.id)}>{l.label}</button>
        ))}
        <span className="kv-label" style={{ marginLeft: 10 }}>window</span>
        {WINDOWS.map((w) => (
          <button key={w.h} className={`pill ${hours === w.h ? 'pill-on' : ''}`}
            onClick={() => setHours(w.h)}>{w.label}</button>
        ))}
        {nodes.length > 1 && (
          <>
            <span className="kv-label" style={{ marginLeft: 10 }}>node</span>
            <button className={`pill ${node === '' ? 'pill-on' : ''}`} onClick={() => setNode('')}>any</button>
            {nodes.map((n) => (
              <button key={n} className={`pill ${node === n ? 'pill-on' : ''}`}
                onClick={() => setNode(n)}>{n}</button>
            ))}
          </>
        )}
        <span className="filter-right">
          <span className="dot dot-sm" style={{ background: live ? STATUS.good : INK.muted, marginRight: 6 }} />
          {live ? 'live' : 'reconnecting…'} · {shown.length} events
        </span>
      </div>
      <div className="event-list">
        {shown.length === 0 && <div className="muted-note">Nothing happened in this window.</div>}
        {shown.map((e) => (
          <div key={e.id} className={`event event-${e.level}`}
            onClick={() => setOpen(open === e.id ? null : e.id)}>
            <span className="event-time">{fmtTime(e.ts)}</span>
            <span className="dot dot-sm" style={{ background: LEVEL_COLOR[e.level] }} title={e.level} />
            <span className="event-where">{e.node || 'fleet'}<span className="event-src"> · {e.source}</span></span>
            <span className={`event-title ${open === e.id ? 'event-title-open' : ''}`}>{e.title}</span>
            {open === e.id && e.detail && <pre className="event-detail">{e.detail}</pre>}
          </div>
        ))}
      </div>
    </>
  )
}
