export const OVERVIEW_ROWS = [
  { key: 'S\uD83D\uDDA5', groupLabel: 'Sunday Service', labelBottom: '\uD83D\uDDA5\uFE0F' },
  { key: 'S\uD83C\uDFA51', groupLabel: 'Sunday Service', labelBottom: '\uD83C\uDFA51\uFE0F\u20E3' },
  { key: 'S\uD83C\uDFA52', groupLabel: 'Sunday Service', labelBottom: '\uD83C\uDFA52\uFE0F\u20E3' },
  { key: 'S?', groupLabel: 'Sunday Service', labelBottom: 'Other', optional: true },
  { key: 'S\u03A3', groupLabel: 'Sunday Service', labelBottom: 'Total' },
  { key: 'F\uD83D\uDDA5', groupLabel: 'Weekday Service', labelBottom: '\uD83D\uDDA5\uFE0F' },
  { key: 'F\uD83C\uDFA5', groupLabel: 'Weekday Service', labelBottom: '\uD83C\uDFA5' },
  { key: 'F?', groupLabel: 'Weekday Service', labelBottom: 'Other', optional: true },
  { key: 'F\u03A3', groupLabel: 'Weekday Service', labelBottom: 'Total' },
  { key: 'O\u03A3', groupLabel: 'Weekday Service', labelBottom: 'Other Service', optional: true },
  { key: '\u03A3', labelTop: 'Total', labelBottom: '' },
]

const emptyOverviewRow = () => OVERVIEW_ROWS.reduce((row, def) => {
  row[def.key] = 0
  return row
}, {})

const emptyOverviewCounts = (names) => names.reduce((acc, name) => {
  acc[name] = emptyOverviewRow()
  return acc
}, {})

export const isOverviewWorker = (worker) => Boolean(worker && worker !== 'TBD' && worker !== 'Select Helper')

const overviewWeekday = (dateKey) => {
  const date = new Date(`${dateKey}T12:00:00`)
  return Number.isNaN(date.getTime()) ? null : date.getDay()
}

export const getOverviewServiceType = (event) => {
  if (!event || event.cancelled) return null
  const title = `${event.custom_title || ''} ${event.title || ''}`.toLowerCase()
  const weekday = overviewWeekday(event.date)
  if (event.day_type === 'Sunday' || title.includes('new year') || weekday === 0) return 'Sunday'
  if (event.day_type === 'Friday' || weekday === 5) return 'Friday'
  return 'Other'
}

export const getOverviewRoleKey = (serviceType, role) => {
  const normalized = (role || '').trim()
  if (serviceType === 'Sunday') {
    if (/^Computer(?:\s+\d+)?$/.test(normalized)) return 'S\uD83D\uDDA5'
    if (normalized === 'Camera' || normalized === 'Camera 1') return 'S\uD83C\uDFA51'
    if (normalized === 'Camera 2') return 'S\uD83C\uDFA52'
    return 'S?'
  }
  if (serviceType === 'Friday') {
    if (/^Computer(?:\s+\d+)?$/.test(normalized)) return 'F\uD83D\uDDA5'
    if (/^Camera(?:\s+\d+)?$/.test(normalized)) return 'F\uD83C\uDFA5'
    return 'F?'
  }
  return 'O\u03A3'
}

export const collectOverviewWorkerNames = (events) => {
  const names = new Set()
  events.forEach(event => {
    if (!getOverviewServiceType(event)) return
    ;(event.assignments || []).forEach(assignment => {
      const worker = assignment.cover || assignment.person
      if (isOverviewWorker(worker)) names.add(worker)
    })
  })
  return names
}

export const visibleOverviewRows = (counts, names) => OVERVIEW_ROWS.filter(row => (
  !row.optional || names.some(name => (counts[name]?.[row.key] || 0) > 0)
))

export const buildOverviewCounts = (events, names) => {
  const counts = emptyOverviewCounts(names)
  events.forEach(event => {
    const serviceType = getOverviewServiceType(event)
    if (!serviceType) return
    ;(event.assignments || []).forEach(assignment => {
      const worker = assignment.cover || assignment.person
      if (!isOverviewWorker(worker)) return
      if (!counts[worker]) counts[worker] = emptyOverviewRow()
      const key = getOverviewRoleKey(serviceType, assignment.role)
      counts[worker][key] += 1
      if (serviceType === 'Sunday') counts[worker]['S\u03A3'] += 1
      if (serviceType === 'Friday') counts[worker]['F\u03A3'] += 1
      counts[worker]['\u03A3'] += 1
    })
  })
  return counts
}
