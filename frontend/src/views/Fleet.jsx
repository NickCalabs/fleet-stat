import React from 'react'
import { Meter } from '../components/Charts.jsx'
import { STATUS, SERIES } from '../theme.js'
import { fmtGB, fmtNum, fmtPct, fmtUptime } from '../format.js'

function StatusDot({ up, label }) {
  const color = up === null || up === undefined ? '#898781' : up ? STATUS.good : STATUS.critical
  return (
    <span className="status">
      <span className="dot" style={{ background: color }} />
      {label ?? (up == null ? 'unknown' : up ? 'up' : 'down')}
    </span>
  )
}

function TempBadge({ temp }) {
  if (temp == null) return null
  const level = temp >= 95 ? 'critical' : temp >= 85 ? 'serious' : temp >= 75 ? 'warning' : null
  return (
    <span className="kv">
      <span className="kv-label">temp</span>
      <span className="kv-val" style={level ? { color: STATUS[level] } : null}>
        {level ? '⚠ ' : ''}{Math.round(temp)}°C{level ? ' hot' : ''}
      </span>
    </span>
  )
}

function identityLine(id) {
  if (!id) return null
  const arch = id.arch === 'moe'
    ? `MoE ${id.params}${id.active ? ` · ${id.active} active` : ''}`
    : `dense ${id.params}`
  return `${arch}${id.quant ? ` · ${id.quant}` : ''}`
}

function HostLive({ h, n }) {
  if (!n) return null
  const g = n.gpu
  let stats = 'no metrics'
  if (g) {
    stats = `GPU ${fmtPct(g.util_pct)} · ${fmtGB(g.vram_used)} / ${fmtGB(g.vram_total)}` +
      (g.temp != null ? ` · ${Math.round(g.temp)}°C` : '')
  } else if (n.mem_pct != null) {
    stats = `RAM ${fmtPct(n.mem_pct)}` +
      (n.cpu_temp != null ? ` · ${Math.round(n.cpu_temp)}°C` : '')
  }
  return (
    <div className="topo-row">
      <span className="dot dot-sm"
        style={{ background: n.up ? STATUS.good : STATUS.critical }} />
      <span className="topo-host">{h}</span>
      <span className="topo-stats">{stats}</span>
    </div>
  )
}

function ModelCard({ m, nodesById }) {
  const busy = (m.running || 0) > 0
  const ident = identityLine(m.identity)
  return (
    <div className={`card ${m.up ? '' : 'card-down'}`}>
      <div className="card-head">
        <span className="card-title">{m.name}</span>
        <StatusDot up={m.up} label={m.up ? (busy ? 'generating' : 'up') : 'down'} />
      </div>
      {ident && <div className="card-sub">{ident}</div>}
      <div className="chips">
        {m.cluster && <span className="chip chip-muted">TP=2 cluster</span>}
        <span className="chip chip-muted">{m.engine}</span>
        {m.engine === 'ollama' && m.ollama_loaded != null && (
          m.ollama_loaded
            ? <span className="chip chip-live">in memory</span>
            : <span className="chip chip-muted">loads on demand</span>
        )}
      </div>
      <div className="topo">
        {m.hosts.map((h) => <HostLive key={h} h={h} n={nodesById[h]} />)}
      </div>
      {m.up ? (
        <div className="kv-grid">
          <span className="kv"><span className="kv-label">running</span>
            <span className="kv-val">{m.running ?? 0}{busy && <span className="pulse" />}</span></span>
          <span className="kv"><span className="kv-label">waiting</span>
            <span className="kv-val">{m.waiting ?? 0}</span></span>
          <span className="kv"><span className="kv-label">gen tok/s</span>
            <span className="kv-val">{(m.gen_tps ?? 0).toFixed(1)}</span></span>
          <span className="kv"><span className="kv-label">prompt tok/s</span>
            <span className="kv-val">{(m.prompt_tps ?? 0).toFixed(0)}</span></span>
        </div>
      ) : (
        <div className="muted-note">not serving — check {m.cluster ? 'spark-cluster status' : m.hosts[0]}</div>
      )}
      {m.up && m.kv_cache_pct != null && (
        <div className="meter-row">
          <span className="kv-label">KV cache</span>
          <Meter pct={m.kv_cache_pct} />
          <span className="kv-val">{fmtPct(m.kv_cache_pct)}</span>
        </div>
      )}
      <div className="card-foot">
        {fmtNum(m.ctx)} ctx · {fmtNum(m.max_output)} out
        {m.identity?.repo ? ` · ${m.identity.repo}` : (m.served_model ? ` · serving ${m.served_model}` : '')}
        {m.aliases?.length ? ` · aliases: ${m.aliases.join(', ')}` : ''}
      </div>
    </div>
  )
}

function NodeCard({ n }) {
  return (
    <div className={`card ${n.up === false ? 'card-down' : ''}`}>
      <div className="card-head">
        <span className="card-title">{n.label}</span>
        <StatusDot up={n.up} />
      </div>
      <div className="card-sub">{n.hw}</div>
      {n.up && n.cpu_pct != null && (
        <>
          <div className="meter-row">
            <span className="kv-label">CPU</span>
            <Meter pct={n.cpu_pct} />
            <span className="kv-val">{fmtPct(n.cpu_pct)}</span>
          </div>
          <div className="meter-row">
            <span className="kv-label">RAM</span>
            <Meter pct={n.mem_pct} />
            <span className="kv-val">{fmtPct(n.mem_pct)}</span>
          </div>
          <div className="kv-grid">
            <span className="kv"><span className="kv-label">load</span>
              <span className="kv-val">{n.load1?.toFixed(1) ?? '—'}</span></span>
            <span className="kv"><span className="kv-label">uptime</span>
              <span className="kv-val">{fmtUptime(n.uptime_s)}</span></span>
            <TempBadge temp={n.cpu_temp} />
          </div>
        </>
      )}
      {n.up && n.ollama_loaded != null && (
        <div className="kv-grid">
          <span className="kv"><span className="kv-label">ollama models loaded</span>
            <span className="kv-val">{n.ollama_loaded}</span></span>
        </div>
      )}
      {n.gpu && (
        <div className="gpu-block">
          <div className="meter-row">
            <span className="kv-label">GPU</span>
            <Meter pct={n.gpu.util_pct} color={SERIES[2]} />
            <span className="kv-val">{fmtPct(n.gpu.util_pct)}</span>
          </div>
          <div className="meter-row">
            <span className="kv-label">VRAM</span>
            <Meter pct={n.gpu.vram_total ? (n.gpu.vram_used / n.gpu.vram_total) * 100 : 0}
              color={SERIES[2]} />
            <span className="kv-val">{fmtGB(n.gpu.vram_used)} / {fmtGB(n.gpu.vram_total)}</span>
          </div>
          <div className="kv-grid">
            <TempBadge temp={n.gpu.temp} />
            <span className="kv"><span className="kv-label">power</span>
              <span className="kv-val">{n.gpu.power_w != null ? Math.round(n.gpu.power_w) + ' W' : '—'}</span></span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Fleet({ fleet }) {
  if (!fleet) return <div className="muted-note">loading…</div>
  const nodesById = Object.fromEntries(fleet.nodes.map((n) => [n.id, n]))
  const models = fleet.models.filter((m) => !m.hidden)
  const hiddenModels = fleet.models.filter((m) => m.hidden)
  return (
    <>
      <h2>Models</h2>
      <div className="cards">
        {models.map((m) => <ModelCard key={m.name} m={m} nodesById={nodesById} />)}
        {hiddenModels.map((m) => (
          <div className="card card-slim" key={m.name}>
            <div className="card-head">
              <span className="card-title">{m.name}</span>
              <StatusDot up={m.up} />
            </div>
            <div className="card-foot">{m.engine} on {m.hosts.join(', ')} · utility model</div>
          </div>
        ))}
      </div>
      <h2>Nodes</h2>
      <div className="cards">
        {fleet.nodes.map((n) => <NodeCard key={n.id} n={n} />)}
      </div>
    </>
  )
}
