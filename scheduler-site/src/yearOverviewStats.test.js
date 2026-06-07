import assert from 'node:assert/strict'
import {
  OVERVIEW_ROWS,
  buildOverviewCounts,
  collectOverviewWorkerNames,
  getOverviewServiceType,
  inactiveOverviewNames,
  overviewPeriodNames,
  overviewTotalNames,
  visibleOverviewRows,
} from './yearOverviewStats.js'

const event = (overrides) => ({
  date: '2026-06-07',
  day_type: 'Sunday',
  custom_title: null,
  title: 'Sunday Service',
  cancelled: false,
  assignments: [],
  ...overrides,
})

const assignment = (role, person, extra = {}) => ({ role, person, ...extra })

const namesForYear = (events, activeNames = []) => [...new Set([
  ...activeNames,
  ...collectOverviewWorkerNames(events),
])]

{
  const sundayKeys = OVERVIEW_ROWS.filter(row => row.groupLabel === 'Sunday').map(row => row.key)
  const weekdayKeys = OVERVIEW_ROWS.filter(row => row.groupLabel === 'Weekday').map(row => row.key)
  const groupedOrder = OVERVIEW_ROWS.filter(row => row.groupLabel).map(row => row.groupLabel)
  assert.deepEqual(sundayKeys, ['S\uD83D\uDDA5', 'S\uD83C\uDFA51', 'S\uD83C\uDFA52', 'S?', 'S\u03A3'])
  assert.deepEqual(weekdayKeys, ['F\uD83D\uDDA5', 'F\uD83C\uDFA5', 'F?', 'F\u03A3'])
  assert.equal(groupedOrder[0], 'Weekday')
  assert.equal(OVERVIEW_ROWS.find(row => row.key === 'F\u03A3').dividerAfter, true)
  assert.equal(OVERVIEW_ROWS.find(row => row.groupLabel === 'Sunday').dividerBefore, undefined)
  assert.equal(OVERVIEW_ROWS.find(row => row.key === 'S\u03A3').dividerAfter, true)
  assert.equal(OVERVIEW_ROWS.at(-1).labelTop, 'Grand Total')
}

{
  const events = [
    event({
      date: '2026-01-01',
      day_type: 'Custom',
      custom_title: "New Year's Day Service",
      title: "New Year's Day Service",
      assignments: [
        assignment('Computer', 'Florian'),
        assignment('Camera 1', 'Marvin'),
        assignment('Camera 2', 'Viktor'),
      ],
    }),
  ]
  const counts = buildOverviewCounts(events, namesForYear(events, ['Florian']))
  assert.equal(getOverviewServiceType(events[0]), 'Sunday')
  assert.equal(counts.Florian['S\uD83D\uDDA5'], 1)
  assert.equal(counts.Marvin['S\uD83C\uDFA51'], 1)
  assert.equal(counts.Viktor['S\uD83C\uDFA52'], 1)
  assert.equal(counts.Florian['\u03A3'] + counts.Marvin['\u03A3'] + counts.Viktor['\u03A3'], 3)
}

{
  const events = [
    event({
      assignments: [
        assignment('Computer', 'Removed Person'),
        assignment('Camera 1', 'Current Person'),
      ],
    }),
  ]
  const names = namesForYear(events, ['Current Person'])
  const counts = buildOverviewCounts(events, names)
  assert.deepEqual(names.sort(), ['Current Person', 'Removed Person'])
  assert.equal(counts['Removed Person']['S\u03A3'], 1)
  assert.equal(counts['Current Person']['S\u03A3'], 1)
}

{
  const events = [
    event({
      date: '2026-03-08',
      assignments: [
        assignment('Computer', 'Current Person'),
        assignment('Computer 2', 'New Person'),
        assignment('Camera 1', 'New Person'),
        assignment('Camera 1', 'Viktor'),
        assignment('Camera 2', 'Viktor'),
      ],
    }),
  ]
  const activeNames = ['Current Person', 'Zero Active', 'New Person']
  const newcomerNameSet = new Set(['New Person'])
  assert.deepEqual(overviewTotalNames(events, activeNames, newcomerNameSet), ['Current Person', 'Zero Active', 'New Person'])
  assert.deepEqual(overviewPeriodNames(events, activeNames, newcomerNameSet), ['Current Person', 'Zero Active', 'New Person', 'Viktor'])
  assert.deepEqual([...inactiveOverviewNames(overviewPeriodNames(events, activeNames, newcomerNameSet), activeNames)], ['Viktor'])
  assert.deepEqual(overviewPeriodNames([], activeNames, newcomerNameSet), ['Current Person', 'Zero Active', 'New Person'])
}

{
  const events = [
    event({
      date: '2027-02-05',
      day_type: 'Friday',
      title: 'Bible Study',
      assignments: [
        assignment('Computer', 'Original Person', { cover: 'Added Person' }),
        assignment('Camera', 'Select Helper'),
        assignment('Camera', 'TBD'),
      ],
    }),
  ]
  const names = namesForYear(events)
  const counts = buildOverviewCounts(events, names)
  assert.equal(getOverviewServiceType(events[0]), 'Weekday')
  assert.deepEqual(names, ['Added Person'])
  assert.equal(counts['Added Person']['F\uD83D\uDDA5'], 1)
  assert.equal(counts['Added Person']['\u03A3'], 1)
  assert.equal(counts['Original Person'], undefined)
}

{
  const events = [
    event({
      cancelled: true,
      assignments: [assignment('Computer', 'Florian')],
    }),
  ]
  const counts = buildOverviewCounts(events, namesForYear(events, ['Florian']))
  assert.equal(getOverviewServiceType(events[0]), null)
  assert.equal(counts.Florian['\u03A3'], 0)
}

{
  const events = [
    event({
      assignments: [
        assignment('Camera 3', 'Extra Camera'),
        assignment('Usher', 'Custom Role'),
      ],
    }),
    event({
      date: '2026-07-03',
      day_type: 'Friday',
      assignments: [
        assignment('Camera 2', 'Friday Camera'),
        assignment('Slides', 'Friday Other'),
      ],
    }),
    event({
      date: '2026-07-10',
      day_type: 'Friday',
      assignments: [
        assignment('Camera1', 'Compact Weekday Camera'),
        assignment('Camera A', 'Named Weekday Camera'),
      ],
    }),
    event({
      date: '2026-09-26',
      day_type: 'Custom',
      custom_title: 'Saturday Conference',
      assignments: [
        assignment('Computer', 'Saturday Computer'),
        assignment('Camera 3', 'Saturday Camera'),
        assignment('Slides', 'Saturday Other'),
      ],
    }),
  ]
  const names = namesForYear(events)
  const counts = buildOverviewCounts(events, names)
  const rows = visibleOverviewRows(counts, names).map(row => row.key)
  assert.equal(counts['Extra Camera']['S?'], 1)
  assert.equal(counts['Custom Role']['S?'], 1)
  assert.equal(counts['Friday Camera']['F\uD83C\uDFA5'], 1)
  assert.equal(counts['Compact Weekday Camera']['F\uD83C\uDFA5'], 1)
  assert.equal(counts['Named Weekday Camera']['F\uD83C\uDFA5'], 1)
  assert.equal(counts['Friday Other']['F?'], 1)
  assert.equal(counts['Saturday Computer']['F\uD83D\uDDA5'], 1)
  assert.equal(counts['Saturday Camera']['F\uD83C\uDFA5'], 1)
  assert.equal(counts['Saturday Other']['F?'], 1)
  assert.equal(counts['Extra Camera']['S\u03A3'], 1)
  assert.equal(counts['Friday Other']['F\u03A3'], 1)
  assert.equal(counts['Saturday Computer']['F\u03A3'], 1)
  assert.equal(counts['Saturday Camera']['F\u03A3'], 1)
  assert.equal(counts['Saturday Other']['F\u03A3'], 1)
  assert.ok(rows.includes('S?'))
  assert.ok(rows.includes('F?'))
  assert.ok(!rows.includes('O\u03A3'))
}

console.log('yearOverviewStats tests passed')
