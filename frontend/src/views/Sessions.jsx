import React, { useState } from 'react'
import { usePoll } from '../api.js'
import { Meter } from '../components/Charts.jsx'
import { STATUS } from '../theme.js'
import { fmtAgo, fmtNum } from '../format.js'

const WINDOWS = [
  { label: '30 min', h: 0.5 },
  { label: '2 h', h: 2 },
  { label: '6 h', h: 6 },
  { label: '24 h', h: 24 },
]

export default function Sessions({ harnessColors }) {
  const [win, setWin] = useState(6)
  const { data, stale } = usePoll(`/api/sessions?hours=${win}`, 10000)
  const sessions = data?.sessions || []

  return (
    <>
      <div className="filter-row">
        <span className="kv-label">window</span>
        {WINDOWS.map((w) => (
          <button key={w.h} className={`pill ${win === w.h ? 'pill-on' : ''}`}
            onClick={() => setWin(w.h)}>{w.label}</button>
        ))}
        <span className="filter-right">
          {sessions.filter((s) => s.active).length} active · {sessions.length} in window
        </span>
      </div>
      <div className={`session-list ${stale ? 'stale' : ''}`}>
        {sessions.length === 0 && (
          <div className="muted-note">No sessions in the last {win} h. The fleet is quiet.</div>
        )}
        {sessions.map((s, i) => {
          const near = s.fill_pct != null && s.fill_pct >= 80
          const color = harnessColors[s.harness] || harnessColors.other
          return (
            <div className={`session ${s.active ? 'session-active' : ''}`} key={s.chat_id || i}>
              <div className="session-main">
                <div className="session-title-row">
                  <span className="chip" style={{ borderColor: color }}>
                    <span className="dot dot-sm" style={{ background: color }} />
                    {s.harness_label}
                  </span>
                  <span className="session-title">
                    {s.title || `${s.model || 'unknown model'} session`}
                  </span>
                  {s.active && <span className="chip chip-live">● live</span>}
                  {s.estimated && <span className="chip chip-muted">est.</span>}
                </div>
                <div className="session-meta">
                  {s.model || '—'} · {s.requests} req · {fmtNum(s.tokens_total)} tok processed
                  {s.user ? ` · ${s.user}` : ''} · {fmtAgo(s.last_ts)}
                </div>
              </div>
              <div className="session-ctx">
                <div className="meter-row">
                  <Meter pct={s.fill_pct ?? 0}
                    color={near ? STATUS.warning : undefined} />
                  <span className="kv-val ctx-val">
                    {fmtNum(s.ctx_tokens)}{s.ctx_ceiling ? ` / ${fmtNum(s.ctx_ceiling)}` : ''}
                    {s.fill_pct != null ? ` · ${s.fill_pct}%` : ''}
                  </span>
                </div>
                {near && (
                  <div className="ctx-warn" style={{ color: STATUS.warning }}>
                    ⚠ near context ceiling
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
