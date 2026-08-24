// Dark-committed palette — the reference dataviz palette's dark column,
// in its validated slot order. Never re-order or cycle past 8.
export const SERIES = [
  '#3987e5', // 1 blue
  '#d95926', // 2 orange
  '#199e70', // 3 aqua
  '#c98500', // 4 yellow
  '#d55181', // 5 magenta
  '#008300', // 6 green
  '#9085e9', // 7 violet
  '#e66767', // 8 red
]
export const OTHER = '#898781'

export const STATUS = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
}

export const INK = {
  primary: '#ffffff',
  secondary: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  baseline: '#383835',
  surface: '#1a1a19',
  page: '#0d0d0d',
}

// Color follows the entity: slots are assigned once, in config order,
// and survive filtering. Past 8 entities, everything folds to "Other".
export function slotMap(keys) {
  const m = {}
  keys.forEach((k, i) => { m[k] = i < 8 ? SERIES[i] : OTHER })
  m.other = OTHER
  return m
}
