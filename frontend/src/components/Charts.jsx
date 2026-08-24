import React, { useEffect, useRef, useState } from 'react'
import { INK } from '../theme.js'
import { fmtTick } from '../format.js'

function useSize(ref) {
  const [w, setW] = useState(640)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver((e) => setW(Math.max(e[0].contentRect.width, 120)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return w
}

function niceMax(v) {
  if (!v || v <= 0) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p
  return 10 * p
}

const PADL = 48
const PADR = 8
const PADT = 12
const PADB = 24 // room for the x-axis band — never clip it

function XTicks({ buckets, bucketS, xFor, plotH }) {
  const n = buckets.length
  const step = Math.max(1, Math.ceil(n / 6))
  const ticks = []
  for (let i = 0; i < n; i += step) ticks.push(i)
  return (
    <g>
      {ticks.map((i) => (
        <text key={i} x={xFor(i)} y={PADT + plotH + 16} fill={INK.muted}
          fontSize="11" textAnchor="middle" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {fmtTick(buckets[i], bucketS)}
        </text>
      ))}
    </g>
  )
}

function YGrid({ max, plotW, plotH, fmt }) {
  const lines = [0.25, 0.5, 0.75, 1].map((f) => f * max)
  return (
    <g>
      {lines.map((v, i) => {
        const y = PADT + plotH * (1 - v / max)
        return (
          <g key={i}>
            <line x1={PADL} x2={PADL + plotW} y1={y} y2={y} stroke={INK.grid} strokeWidth="1" />
            <text x={PADL - 6} y={y + 4} fill={INK.muted} fontSize="11" textAnchor="end"
              style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(v)}</text>
          </g>
        )
      })}
      <line x1={PADL} x2={PADL + plotW} y1={PADT + plotH} y2={PADT + plotH}
        stroke={INK.baseline} strokeWidth="1" />
    </g>
  )
}

function Tooltip({ pos, children }) {
  if (!pos) return null
  return (
    <div className="tooltip" style={{ left: pos.left, top: pos.top }}>
      {children}
    </div>
  )
}

// Stacked (or single-series) bar chart over time buckets.
// series: [{ key, label, color, values: number[] }]
export function StackedBars({ buckets, bucketS, series, height = 250, fmt }) {
  const ref = useRef()
  const W = useSize(ref)
  const [hover, setHover] = useState(null)
  const plotW = W - PADL - PADR
  const plotH = height - PADT - PADB
  const n = buckets.length || 1
  const totals = buckets.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0))
  const max = niceMax(Math.max(...totals, 1))
  const slotW = plotW / n
  const bw = Math.max(slotW - 2, 1) // 2px surface gap between bars
  const xFor = (i) => PADL + slotW * i + slotW / 2
  const yFor = (v) => PADT + plotH * (1 - v / max)

  return (
    <div className="chart" ref={ref} style={{ position: 'relative' }}>
      <svg width={W} height={height}>
        <YGrid max={max} plotW={plotW} plotH={plotH} fmt={fmt} />
        {buckets.map((_, i) => {
          if (!totals[i]) return null
          const x = PADL + slotW * i + 1
          const topY = yFor(totals[i])
          const clipId = `bar-${i}-${bw.toFixed(0)}`
          let acc = 0
          return (
            <g key={i}>
              <defs>
                <clipPath id={clipId}>
                  <rect x={x} y={topY} width={bw} height={PADT + plotH - topY + 4} rx="4" />
                </clipPath>
              </defs>
              <g clipPath={`url(#${clipId})`}>
                {series.map((s) => {
                  const v = s.values[i] || 0
                  if (!v) return null
                  const y0 = yFor(acc + v)
                  const h = yFor(acc) - y0
                  acc += v
                  return (
                    <rect key={s.key} x={x} y={y0} width={bw} height={h}
                      fill={s.color} stroke={INK.surface} strokeWidth="1" />
                  )
                })}
              </g>
            </g>
          )
        })}
        <XTicks buckets={buckets} bucketS={bucketS} xFor={xFor} plotH={plotH} />
        {hover != null && (
          <rect x={PADL + slotW * hover} y={PADT} width={slotW} height={plotH}
            fill="rgba(255,255,255,0.06)" pointerEvents="none" />
        )}
        {buckets.map((_, i) => (
          <rect key={i} x={PADL + slotW * i} y={0} width={slotW} height={height}
            fill="transparent"
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      <Tooltip pos={hover != null ? {
        left: Math.min(PADL + slotW * hover + slotW + 8, W - 190),
        top: PADT,
      } : null}>
        {hover != null && (
          <>
            <div className="tt-title">{fmtTick(buckets[hover], bucketS)}</div>
            {series.filter((s) => s.values[hover]).map((s) => (
              <div className="tt-row" key={s.key}>
                <span className="dot" style={{ background: s.color }} />
                <span className="tt-label">{s.label}</span>
                <span className="tt-val">{fmt(s.values[hover])}</span>
              </div>
            ))}
            {series.length > 1 && (
              <div className="tt-row tt-total">
                <span className="tt-label">total</span>
                <span className="tt-val">{fmt(totals[hover])}</span>
              </div>
            )}
            {!totals[hover] && <div className="tt-row"><span className="tt-label">no traffic</span></div>}
          </>
        )}
      </Tooltip>
    </div>
  )
}

// Single-series line with crosshair + tooltip. values may contain nulls.
export function LineChart({ buckets, bucketS, values, color, height = 180, fmt }) {
  const ref = useRef()
  const W = useSize(ref)
  const [hover, setHover] = useState(null)
  const plotW = W - PADL - PADR
  const plotH = height - PADT - PADB
  const n = buckets.length || 1
  const slotW = plotW / n
  const max = niceMax(Math.max(...values.filter((v) => v != null), 1))
  const xFor = (i) => PADL + slotW * i + slotW / 2
  const yFor = (v) => PADT + plotH * (1 - v / max)

  const segments = []
  let cur = []
  values.forEach((v, i) => {
    if (v == null) { if (cur.length) segments.push(cur); cur = [] }
    else cur.push([xFor(i), yFor(v)])
  })
  if (cur.length) segments.push(cur)

  return (
    <div className="chart" ref={ref} style={{ position: 'relative' }}>
      <svg width={W} height={height}>
        <YGrid max={max} plotW={plotW} plotH={plotH} fmt={fmt} />
        {segments.map((seg, i) =>
          seg.length === 1 ? (
            <circle key={i} cx={seg[0][0]} cy={seg[0][1]} r="3" fill={color} />
          ) : (
            <polyline key={i} points={seg.map((p) => p.join(',')).join(' ')}
              fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
          )
        )}
        <XTicks buckets={buckets} bucketS={bucketS} xFor={xFor} plotH={plotH} />
        {hover != null && values[hover] != null && (
          <>
            <line x1={xFor(hover)} x2={xFor(hover)} y1={PADT} y2={PADT + plotH}
              stroke={INK.baseline} strokeWidth="1" pointerEvents="none" />
            <circle cx={xFor(hover)} cy={yFor(values[hover])} r="4" fill={color}
              stroke={INK.surface} strokeWidth="2" pointerEvents="none" />
          </>
        )}
        {buckets.map((_, i) => (
          <rect key={i} x={PADL + slotW * i} y={0} width={slotW} height={height}
            fill="transparent"
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      <Tooltip pos={hover != null && values[hover] != null ? {
        left: Math.min(xFor(hover) + 10, W - 170), top: PADT,
      } : null}>
        {hover != null && values[hover] != null && (
          <>
            <div className="tt-title">{fmtTick(buckets[hover], bucketS)}</div>
            <div className="tt-row">
              <span className="dot" style={{ background: color }} />
              <span className="tt-val">{fmt(values[hover])}</span>
            </div>
          </>
        )}
      </Tooltip>
    </div>
  )
}

export function Legend({ items }) {
  if (items.length < 2) return null
  return (
    <div className="legend">
      {items.map((it) => (
        <span className="legend-item" key={it.key}>
          <span className="dot" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

// Thin horizontal magnitude meter.
export function Meter({ pct, color = '#3987e5' }) {
  const v = Math.max(0, Math.min(100, pct ?? 0))
  return (
    <div className="meter">
      <div className="meter-fill" style={{ width: `${v}%`, background: color }} />
    </div>
  )
}
