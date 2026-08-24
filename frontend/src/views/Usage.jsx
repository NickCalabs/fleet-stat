import React, { useMemo, useState } from 'react'
import { usePoll } from '../api.js'
import { Legend, LineChart, StackedBars } from '../components/Charts.jsx'
import { SERIES, slotMap } from '../theme.js'
import { fmtNum, fmtSecs } from '../format.js'

const RANGES = [
  { label: '6 h', h: 6 },
  { label: '24 h', h: 24 },
  { label: '7 d', h: 168 },
  { label: '30 d', h: 720 },
]

export default function Usage({ config, labels }) {
  const [hours, setHours] = useState(24)
  const [group, setGroup] = useState('harness')
  const { data, stale } = usePoll(`/api/usage?hours=${hours}&group=${group}`, 30000)

  const slotKeys = useMemo(() => {
    if (!config) return []
    return group === 'harness'
      ? config.harnesses.map((h) => h.id)
      : config.models.filter((m) => !m.hidden).map((m) => m.name)
  }, [config, group])
  const colors = useMemo(() => slotMap(slotKeys), [slotKeys])

  if (!data) return <div className="muted-note">loading…</div>

  const label = (k) => (group === 'harness' ? labels[k] || k : k)
  const series = data.series.map((s) => ({
    ...s,
    label: label(s.key),
    color: colors[s.key] || colors.other,
    values: s.tokens,
  }))

  const totalReq = data.totals.reduce((a, t) => a + t.requests, 0)
  const totalTok = data.totals.reduce((a, t) => a + t.total_tokens, 0)
  const wLat = totalReq
    ? data.totals.reduce((a, t) => a + (t.avg_latency_s || 0) * t.requests, 0) / totalReq
    : null
  const wTps = (() => {
    const rows = data.totals.filter((t) => t.avg_gen_tps)
    const n = rows.reduce((a, t) => a + t.requests, 0)
    return n ? rows.reduce((a, t) => a + t.avg_gen_tps * t.requests, 0) / n : null
  })()

  const nb = data.buckets.length
  const reqSeries = [{
    key: 'req', label: 'requests', color: SERIES[0],
    values: data.buckets.map((_, i) => data.series.reduce((a, s) => a + (s.requests[i] || 0), 0)),
  }]
  const latValues = data.buckets.map((_, i) => {
    let n = 0, sum = 0
    for (const s of data.series) {
      if (s.latency[i] != null && s.requests[i]) { n += s.requests[i]; sum += s.latency[i] * s.requests[i] }
    }
    return n ? sum / n : null
  })

  return (
    <>
      <div className="filter-row">
        <span className="kv-label">range</span>
        {RANGES.map((r) => (
          <button key={r.h} className={`pill ${hours === r.h ? 'pill-on' : ''}`}
            onClick={() => setHours(r.h)}>{r.label}</button>
        ))}
        <span className="kv-label" style={{ marginLeft: 16 }}>by</span>
        <button className={`pill ${group === 'harness' ? 'pill-on' : ''}`}
          onClick={() => setGroup('harness')}>Harness</button>
        <button className={`pill ${group === 'model' ? 'pill-on' : ''}`}
          onClick={() => setGroup('model')}>Model</button>
      </div>

      <div className={stale ? 'stale' : ''}>
        <div className="tiles">
          <div className="tile"><div className="tile-val">{fmtNum(totalTok)}</div>
            <div className="tile-label">tokens</div></div>
          <div className="tile"><div className="tile-val">{fmtNum(totalReq)}</div>
            <div className="tile-label">requests</div></div>
          <div className="tile"><div className="tile-val">{fmtSecs(wLat)}</div>
            <div className="tile-label">avg latency</div></div>
          <div className="tile"><div className="tile-val">{wTps ? wTps.toFixed(1) : '—'}</div>
            <div className="tile-label">avg gen tok/s</div></div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h3>Tokens</h3>
            <Legend items={series.map((s) => ({ key: s.key, label: s.label, color: s.color }))} />
          </div>
          <StackedBars buckets={data.buckets} bucketS={data.bucket_s}
            series={series} fmt={fmtNum} />
        </div>

        <div className="panel-pair">
          <div className="panel">
            <div className="panel-head"><h3>Requests</h3></div>
            <StackedBars buckets={data.buckets} bucketS={data.bucket_s}
              series={reqSeries} fmt={fmtNum} height={190} />
          </div>
          <div className="panel">
            <div className="panel-head"><h3>Avg latency</h3></div>
            <LineChart buckets={data.buckets} bucketS={data.bucket_s}
              values={latValues} color={SERIES[0]} fmt={fmtSecs} height={190} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>Totals</h3></div>
          <table className="totals">
            <thead>
              <tr>
                <th>{group}</th><th>requests</th><th>prompt tok</th>
                <th>completion tok</th><th>total tok</th><th>avg latency</th><th>gen tok/s</th>
              </tr>
            </thead>
            <tbody>
              {data.totals.map((t) => (
                <tr key={t.grp}>
                  <td>
                    <span className="dot dot-sm" style={{ background: colors[t.grp] || colors.other }} />
                    {' '}{label(t.grp)}
                  </td>
                  <td>{t.requests}</td>
                  <td>{fmtNum(t.prompt_tokens)}</td>
                  <td>{fmtNum(t.completion_tokens)}</td>
                  <td>{fmtNum(t.total_tokens)}</td>
                  <td>{fmtSecs(t.avg_latency_s)}</td>
                  <td>{t.avg_gen_tps ? t.avg_gen_tps.toFixed(1) : '—'}</td>
                </tr>
              ))}
              {data.totals.length === 0 && (
                <tr><td colSpan="7" className="muted-note">no traffic in range</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
