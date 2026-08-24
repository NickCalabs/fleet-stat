import { useEffect, useRef, useState } from 'react'

// Polls a JSON endpoint. Keeps the previous payload while refetching
// (no skeleton flash); `stale` flags a failing source.
export function usePoll(url, ms) {
  const [data, setData] = useState(null)
  const [stale, setStale] = useState(false)
  const urlRef = useRef(url)
  urlRef.current = url
  useEffect(() => {
    let alive = true
    let timer
    const go = async () => {
      const u = urlRef.current
      try {
        const r = await fetch(u)
        if (!r.ok) throw new Error('HTTP ' + r.status)
        const j = await r.json()
        if (alive && urlRef.current === u) { setData(j); setStale(false) }
      } catch {
        if (alive) setStale(true)
      }
      if (alive) timer = setTimeout(go, ms)
    }
    go()
    return () => { alive = false; clearTimeout(timer) }
  }, [url, ms])
  return { data, stale }
}
