import React, { useMemo, useState } from 'react'
import { usePoll } from './api.js'
import Fleet from './views/Fleet.jsx'
import Sessions from './views/Sessions.jsx'
import Usage from './views/Usage.jsx'
import { STATUS, slotMap } from './theme.js'

const TABS = [
  { id: 'fleet', label: 'Fleet' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'usage', label: 'Usage' },
]

function SourceDot({ name, ok }) {
  return (
    <span className="source" title={`${name}: ${ok ? 'ok' : 'unreachable'}`}>
      <span className="dot dot-sm" style={{ background: ok ? STATUS.good : STATUS.critical }} />
      {name}
    </span>
  )
}

export default function App() {
  const initial = window.location.hash.replace('#', '')
  const [tab, setTabState] = useState(TABS.some((t) => t.id === initial) ? initial : 'fleet')
  const setTab = (id) => { setTabState(id); window.location.hash = id }
  const { data: fleet, stale } = usePoll('/api/fleet', 10000)
  const { data: config } = usePoll('/api/config', 300000)

  const harnessColors = useMemo(
    () => slotMap((config?.harnesses || []).map((h) => h.id)),
    [config]
  )
  const harnessLabels = useMemo(
    () => Object.fromEntries((config?.harnesses || []).map((h) => [h.id, h.label])),
    [config]
  )

  const modelsUp = fleet ? fleet.models.filter((m) => !m.hidden && m.up).length : 0
  const modelsAll = fleet ? fleet.models.filter((m) => !m.hidden).length : 0

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand-mark">⬢</span> Fleet Stat
        </div>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? 'tab-on' : ''}`}
              onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <div className="header-right">
          {fleet && (
            <span className="summary">{modelsUp}/{modelsAll} models up</span>
          )}
          {fleet && (
            <>
              <SourceDot name="prom" ok={fleet.sources.prometheus.up} />
              <SourceDot name="litellm" ok={fleet.sources.litellm.up} />
              {fleet.sources.openwebui.up != null && (
                <SourceDot name="owui" ok={fleet.sources.openwebui.up} />
              )}
            </>
          )}
        </div>
      </header>
      <main className={stale ? 'stale' : ''}>
        {tab === 'fleet' && <Fleet fleet={fleet} />}
        {tab === 'sessions' && <Sessions harnessColors={harnessColors} />}
        {tab === 'usage' && <Usage config={config} labels={harnessLabels} />}
      </main>
    </div>
  )
}
