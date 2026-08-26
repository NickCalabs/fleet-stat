import React, { useState } from 'react'
import { usePoll } from '../api.js'
import { STATUS, SERIES, INK } from '../theme.js'
import { fmtGB, fmtAgo } from '../format.js'

const STATUSES = ['untested', 'testing', 'keep', 'prune']
const STATUS_COLOR = {
  keep: STATUS.good, testing: SERIES[0], prune: STATUS.warning, untested: '#898781',
}

function Row({ node, m, onTag }) {
  const [editing, setEditing] = useState(false)
  const [notes, setNotes] = useState(m.notes)
  const cycle = () => {
    const next = STATUSES[(STATUSES.indexOf(m.status) + 1) % STATUSES.length]
    onTag(node, m.model_id, next, m.notes)
  }
  const saveNotes = () => {
    setEditing(false)
    if (notes !== m.notes) onTag(node, m.model_id, m.status, notes)
  }
  return (
    <tr className={m.loaded ? 'lib-loaded' : ''}>
      <td className="lib-name">
        {m.loaded && <span className="dot dot-sm" style={{ background: STATUS.good }} />}
        {' '}{m.model_id}
        {m.loaded && <span className="chip chip-live lib-chip">loaded</span>}
      </td>
      <td>{m.params || '—'}</td>
      <td>{m.quant || '—'}</td>
      <td>{fmtGB(m.size_bytes)}</td>
      <td>{m.mtime ? fmtAgo(m.mtime) : '—'}</td>
      <td>
        <button className="status-pill" title="click to cycle"
          style={{ color: STATUS_COLOR[m.status], borderColor: STATUS_COLOR[m.status] }}
          onClick={cycle}>{m.status}</button>
      </td>
      <td className="lib-notes" onClick={() => !editing && setEditing(true)}>
        {editing ? (
          <input autoFocus value={notes} onChange={(e) => setNotes(e.target.value)}
            onBlur={saveNotes} onKeyDown={(e) => e.key === 'Enter' && saveNotes()} />
        ) : (m.notes || <span style={{ color: INK.muted }}>add note…</span>)}
      </td>
    </tr>
  )
}

export default function Library() {
  const [bump, setBump] = useState(0)
  const { data, stale } = usePoll(`/api/library?b=${bump}`, 30000)
  const onTag = async (node, model_id, status, notes) => {
    await fetch('/api/library/tag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node, model_id, status, notes }),
    })
    setBump((b) => b + 1)
  }
  if (!data) return <div className="muted-note">loading…</div>
  if (!data.nodes.length) {
    return <div className="muted-note">
      No inventory yet. The collector pushes HF-cache inventories; ollama nodes appear
      automatically within a minute.
    </div>
  }
  return (
    <div className={stale ? 'stale' : ''}>
      {data.nodes.map((n) => (
        <div className="panel" key={n.id}>
          <div className="panel-head">
            <h3>{n.label}</h3>
            <span className="filter-right">
              {n.models.length} models · {fmtGB(n.disk_bytes)} on disk
            </span>
          </div>
          <table className="totals lib-table">
            <thead>
              <tr><th>model</th><th>params</th><th>quant</th><th>size</th>
                <th>modified</th><th>status</th><th>notes</th></tr>
            </thead>
            <tbody>
              {n.models.map((m) => (
                <Row key={m.model_id} node={n.id} m={m} onTag={onTag} />
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
