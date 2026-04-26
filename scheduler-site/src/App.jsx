import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'

const API = '/api/v2'
const AUTH_TOKEN_KEY = 'livestreamV2AuthToken'
const TELEGRAM_LOGIN_KEYS = ['id', 'first_name', 'last_name', 'username', 'photo_url', 'auth_date', 'hash']

const ROLE_ICONS = {
  Computer: '\uD83D\uDDA5\uFE0F',
  'Camera 1': '\uD83C\uDFA5',
  'Camera 2': '\uD83C\uDFA5',
  Camera: '\uD83C\uDFA5',
  Leader: '\uD83D\uDCD6',
  Helper: '\uD83E\uDD1D',
}

const STATUS_LABELS = {
  confirmed: 'Confirmed',
  pending: 'Pending',
  swap_needed: 'Needs Coverage',
}

const toDateKey = (date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const toMonthKey = (date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  return `${year}-${month}`
}

// ═══════════════════════════════════════════════════════════════
//  API Helpers
// ═══════════════════════════════════════════════════════════════

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

// ═══════════════════════════════════════════════════════════════
//  App
// ═══════════════════════════════════════════════════════════════

export default function App() {
  const [user, setUser] = useState(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [isManager, setIsManager] = useState(false)
  const [schedule, setSchedule] = useState([])
  const [team, setTeam] = useState([])
  const [loading, setLoading] = useState(true)
  const [flash, setFlash] = useState(null)
  const [selectedMonth, setSelectedMonth] = useState(null)
  const [hasSavedAuth, setHasSavedAuth] = useState(false)
  const [headerStuck, setHeaderStuck] = useState(false)
  const [showCreate, setShowCreate] = useState(false)

  useEffect(() => {
    const onScroll = () => setHeaderStuck(window.scrollY > 4)
    window.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // ── Init ──────────────────────────────────────────────────
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const urlToken = params.get('auth')
    const telegramHash = params.get('hash')
    const storedToken = localStorage.getItem(AUTH_TOKEN_KEY)
    const token = urlToken || storedToken
    const finish = (d) => {
      setUser(d.name || null)
      setIsAdmin(Boolean(d.is_admin))
      setIsManager(Boolean(d.is_manager))
      if (d.auth_token) {
        localStorage.setItem(AUTH_TOKEN_KEY, d.auth_token)
        setHasSavedAuth(true)
      }
    }
    const clearAuthParams = () => {
      params.delete('auth')
      TELEGRAM_LOGIN_KEYS.forEach(key => params.delete(key))
      const query = params.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    }
    if (urlToken) {
      localStorage.setItem(AUTH_TOKEN_KEY, urlToken)
      setHasSavedAuth(true)
    } else {
      setHasSavedAuth(Boolean(storedToken))
    }

    const telegramLoginData = {}
    TELEGRAM_LOGIN_KEYS.forEach(key => {
      const value = params.get(key)
      if (value) telegramLoginData[key] = value
    })

    const request = telegramHash
      ? api('/auth/telegram-login', {
          method: 'POST',
          body: JSON.stringify(telegramLoginData),
        }).then(d => {
          clearAuthParams()
          return d
        })
      : token
      ? api('/auth/token-login', {
          method: 'POST',
          body: JSON.stringify({ token }),
        }).then(d => {
          if (urlToken) clearAuthParams()
          return d
        })
      : api('/auth/me')

    request.then(finish).catch(() => {
      if (token || telegramHash) {
        localStorage.removeItem(AUTH_TOKEN_KEY)
        setHasSavedAuth(false)
      }
    }).finally(() => setLoading(false))
  }, [])

  const loadSchedule = useCallback(() => {
    api('/schedule').then(setSchedule).catch(console.error)
  }, [])

  const loadTeam = useCallback(() => {
    api('/team').then(setTeam).catch(console.error)
  }, [])

  useEffect(() => {
    if (!loading) {
      loadSchedule()
      loadTeam()
    }
  }, [loading, loadSchedule, loadTeam])

  const showFlash = (msg, type = 'success') => {
    setFlash({ msg, type })
    setTimeout(() => setFlash(null), 3000)
  }

  const handleLogout = async () => {
    await api('/auth/logout', { method: 'POST' }).catch(() => {})
    setUser(null)
    setIsAdmin(false)
    setIsManager(false)
  }

  const restoreSavedLogin = async () => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    if (!token) {
      setHasSavedAuth(false)
      return
    }
    try {
      const d = await api('/auth/token-login', {
        method: 'POST',
        body: JSON.stringify({ token }),
      })
      setUser(d.name || null)
      setIsAdmin(Boolean(d.is_admin))
      setIsManager(Boolean(d.is_manager))
      setHasSavedAuth(true)
    } catch (e) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      setHasSavedAuth(false)
      showFlash(e.message, 'error')
    }
  }

  const toggleManager = async () => {
    try {
      const d = await api('/auth/manager', {
        method: 'POST',
        body: JSON.stringify({ pin: '2026' }),
      })
      setIsManager(d.is_manager)
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  // ── Actions ───────────────────────────────────────────────
  const doAction = async (action, assignmentId, extra = {}) => {
    try {
      await api('/action', {
        method: 'POST',
        body: JSON.stringify({ action, assignment_id: assignmentId, ...extra }),
      })
      loadSchedule()
      if (action === 'confirm') showFlash('Confirmed!')
      else if (action === 'pickup') showFlash('Shift picked up! Thank you!')
      else if (action === 'decline') showFlash('Marked as unavailable')
      else if (action === 'volunteer') showFlash('Volunteered! Thank you!')
      else loadSchedule()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  // ── Render ────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="app">
        <header className="app-header">
          <h1 className="app-title">Livestream Schedule</h1>
        </header>
        <div className="skeleton-list" aria-hidden="true">
          <div className="skeleton-card" />
          <div className="skeleton-card" />
          <div className="skeleton-card" />
        </div>
      </div>
    )
  }

  // Calculate months for navigation
  const now = new Date()
  const today = toDateKey(now)
  const currentYear = now.getFullYear()
  const currentMonth = toMonthKey(now)
  const maxVisibleMonth = toMonthKey(new Date(now.getFullYear(), now.getMonth() + 3, 1))
  const currentYearStart = `${currentYear}-01`
  const months = [...new Set(schedule.map(e => e.date.slice(0, 7)))]
    .filter(m => {
      const year = Number(m.slice(0, 4))
      if (year < currentYear) return true
      return m >= currentYearStart && m <= maxVisibleMonth
    })
    .sort()
  const activeMonth = selectedMonth && months.includes(selectedMonth)
    ? selectedMonth
    : (months.includes(currentMonth) ? currentMonth : months[0])

  const filtered = activeMonth
    ? schedule.filter(e => e.date.startsWith(activeMonth))
    : schedule
  const visibleSchedule = activeMonth === currentMonth
    ? [
        ...filtered.filter(e => e.date >= today).sort((a, b) => a.date.localeCompare(b.date)),
        ...filtered.filter(e => e.date < today).sort((a, b) => b.date.localeCompare(a.date)),
      ]
    : filtered.sort((a, b) => a.date.localeCompare(b.date))

  return (
    <div className="app">
      {/* Flash message */}
      {flash && (
        <div className={`flash flash-${flash.type}`} role="status" aria-live="polite">{flash.msg}</div>
      )}

      {/* Header */}
      <header className={`app-header ${headerStuck ? 'is-stuck' : ''}`}>
        <h1 className="app-title">Livestream Schedule</h1>
        <div className="header-actions">
          {user && <span className="user-chip">{user}</span>}
          {isManager && <span className="manager-chip">Manager On</span>}
          {isManager && (
            <button
              className="icon-btn primary"
              onClick={() => setShowCreate(true)}
              title="New event"
              aria-label="Create event"
            >
              {'+'}
            </button>
          )}
          {isAdmin && (
            <button
              className={`manager-toggle ${isManager ? 'active' : ''}`}
              onClick={toggleManager}
              title={isManager ? 'Exit Manager' : 'Manager Mode'}
              aria-label={isManager ? 'Exit Manager Mode' : 'Enter Manager Mode'}
            >
              <span>{isManager ? '\uD83D\uDEE1\uFE0F' : '\uD83D\uDD12'}</span>
              <span>{isManager ? 'Manager' : 'Admin'}</span>
            </button>
          )}
          {!user && hasSavedAuth && (
            <button className="icon-btn" onClick={restoreSavedLogin} title="Restore Admin">
              {'\uD83D\uDD12'}
            </button>
          )}
          {user && (
            <button className="icon-btn" onClick={handleLogout} title="Logout">
              {'\uD83D\uDEAA'}
            </button>
          )}
        </div>
      </header>

      <ScheduleTab
        schedule={visibleSchedule}
        months={months}
        activeMonth={activeMonth}
        onMonthChange={setSelectedMonth}
        user={user}
        isManager={isManager}
        doAction={doAction}
        showFlash={showFlash}
        loadSchedule={loadSchedule}
        team={team}
      />

      {showCreate && (
        <CreateEventModal
          team={team}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false)
            loadSchedule()
            showFlash('Event created')
          }}
          showFlash={showFlash}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Schedule Tab
// ═══════════════════════════════════════════════════════════════

function ScheduleTab({ schedule, months, activeMonth, onMonthChange, user, isManager, doAction, showFlash, loadSchedule, team }) {
  const [expandedYears, setExpandedYears] = useState({})

  const handleNotify = async (date) => {
    try {
      await api('/telegram/notify', {
        method: 'POST',
        body: JSON.stringify({ date }),
      })
      showFlash('Telegram notification sent!')
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleAssign = async (assignmentId, person) => {
    try {
      await api(`/assignment/${assignmentId}`, {
        method: 'PATCH',
        body: JSON.stringify({ person }),
      })
      loadSchedule()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleEventUpdate = async (event, updates) => {
    try {
      await api(`/event/${event.date}`, {
        method: 'PATCH',
        body: JSON.stringify(updates),
      })
      loadSchedule()
      showFlash('Event updated')
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const teamNames = team.length > 0
    ? team.map(m => m.name).sort()
    : ['Andy', 'Florian', 'Marvin', 'Patric', 'Rene', 'Stefan', 'Viktor', 'TBD']

  const currentYear = new Date().getFullYear()
  const monthGroups = months.reduce((groups, month) => {
    const year = month.slice(0, 4)
    groups[year] = groups[year] || []
    groups[year].push(month)
    return groups
  }, {})
  const pastYears = Object.keys(monthGroups).filter(year => Number(year) < currentYear).sort()
  const currentAndFutureMonths = months.filter(month => Number(month.slice(0, 4)) >= currentYear)
  const toggleYear = (year) => {
    setExpandedYears(prev => ({ ...prev, [year]: !prev[year] }))
  }
  const renderMonthPill = (month) => {
    const label = new Date(month + '-15').toLocaleString('en', { month: 'short' })
    const isPast = month < new Date().toISOString().slice(0, 7)
    return (
      <button
        key={month}
        className={`month-pill ${month === activeMonth ? 'active' : ''} ${isPast ? 'past' : ''}`}
        onClick={() => onMonthChange(month)}
      >
        {label}
      </button>
    )
  }

  return (
    <div className="schedule-tab">
      {/* Month navigation */}
      <div className="month-nav">
        {pastYears.map(year => {
          const yearMonths = monthGroups[year]
          const hasActiveMonth = yearMonths.includes(activeMonth)
          return (
            <div key={year} className="year-group">
              <button
                className={`month-pill year-pill ${hasActiveMonth ? 'active' : ''}`}
                onClick={() => toggleYear(year)}
              >
                {year}
              </button>
              {expandedYears[year] && (
                <div className="year-months">
                  {yearMonths.map(renderMonthPill)}
                </div>
              )}
            </div>
          )
        })}
        {currentAndFutureMonths.map(renderMonthPill)}
      </div>

      {/* Events */}
      <div className="events-list">
        {schedule.length === 0 && (
          <div className="empty-state">
            <p>No events for this month.</p>
            {isManager && <p>Generate one or use Telegram to add an event.</p>}
          </div>
        )}
        {schedule.map(event => (
          <EventCard
            key={event.date}
            event={event}
            user={user}
            isManager={isManager}
            doAction={doAction}
            onNotify={() => handleNotify(event.date)}
            onAssign={handleAssign}
            onEventUpdate={handleEventUpdate}
            teamNames={teamNames}
          />
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Event Card
// ═══════════════════════════════════════════════════════════════

function EventCard({ event, user, isManager, doAction, onNotify, onAssign, onEventUpdate, teamNames }) {
  const [editingEvent, setEditingEvent] = useState(false)
  const [editType, setEditType] = useState(event.day_type === 'Sunday' ? 'Sunday' : event.day_type === 'Friday' ? 'Bible Study' : 'Other')
  const [editTitle, setEditTitle] = useState(event.custom_title || event.title || '')
  const [editDate, setEditDate] = useState(event.date)
  const editorRef = useRef(null)
  const d = new Date(event.date + 'T12:00:00')
  const dayNum = d.getDate()
  const dayName = d.toLocaleString('en', { weekday: 'short' })
  const monthName = d.toLocaleString('en', { month: 'short' })
  const saveEvent = () => {
    const updates = {
      new_date: editDate,
      day_type: editType === 'Sunday' ? 'Sunday' : editType === 'Bible Study' ? 'Friday' : 'Custom',
      custom_title: editType === 'Other' ? editTitle : null,
    }
    onEventUpdate(event, updates)
    setEditingEvent(false)
  }

  useEffect(() => {
    if (!editingEvent) return
    const handlePointer = (e) => {
      if (editorRef.current?.contains(e.target)) return
      const card = editorRef.current?.closest('.event-card')
      if (card?.contains(e.target)) return
      setEditingEvent(false)
    }
    const handleKey = (e) => { if (e.key === 'Escape') setEditingEvent(false) }
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('touchstart', handlePointer)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('touchstart', handlePointer)
      document.removeEventListener('keydown', handleKey)
    }
  }, [editingEvent])

  const canEdit = isManager && !event.is_past
  const DateTag = canEdit ? 'button' : 'div'
  const TitleTag = canEdit ? 'button' : 'span'
  const editProps = canEdit
    ? { type: 'button', onClick: () => setEditingEvent(true), 'aria-label': 'Edit event' }
    : {}
  const todayKey = (() => {
    const n = new Date()
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`
  })()
  const isToday = event.date === todayKey
  const hasSwap = event.assignments.some(a => a.status === 'swap_needed')
  const cardClasses = [
    'event-card',
    event.is_past ? 'past' : '',
    isToday ? 'today' : '',
    hasSwap && !event.is_past ? 'swap-needed' : '',
  ].filter(Boolean).join(' ')
  return (
    <div className={cardClasses}>
      <DateTag
        className={`event-date-col ${canEdit ? 'editable' : ''}`}
        {...editProps}
      >
        <span className="event-day-name">{dayName}</span>
        <span className="event-day-num">{dayNum}</span>
        <span className="event-month">{monthName}</span>
      </DateTag>
      <div className="event-info">
        <div className="event-header">
          <TitleTag
            className={`event-title ${canEdit ? 'editable' : ''}`}
            {...editProps}
          >
            {event.title}
            {isToday && <span className="today-pill">TODAY</span>}
          </TitleTag>
          {isManager && !event.is_past && (
            <button className="icon-btn-sm" onClick={onNotify} title="Send Telegram">
              {'\uD83D\uDCE8'}
            </button>
          )}
        </div>
        {editingEvent && (
          <div className="event-editor" ref={editorRef}>
            <input
              type="date"
              value={editDate}
              onChange={e => setEditDate(e.target.value)}
              className="event-edit-input"
            />
            <select
              value={editType}
              onChange={e => setEditType(e.target.value)}
              className="event-edit-input"
            >
              <option value="Sunday">Sunday Service</option>
              <option value="Bible Study">Bible Study</option>
              <option value="Other">Other</option>
            </select>
            {editType === 'Other' && (
              <input
                type="text"
                value={editTitle}
                onChange={e => setEditTitle(e.target.value)}
                placeholder="Event title"
                className="event-edit-input"
              />
            )}
            <div className="event-editor-actions">
              <button className="action-btn confirm" onClick={saveEvent}>Save</button>
              <button className="action-btn undo" onClick={() => setEditingEvent(false)}>Cancel</button>
            </div>
          </div>
        )}
        <div className="assignments">
          {event.assignments.map(a => (
            <AssignmentRow
              key={a.id}
              assignment={a}
              user={user}
              isManager={isManager}
              doAction={doAction}
              onAssign={onAssign}
              teamNames={teamNames}
              isPast={event.is_past}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Assignment Row
// ═══════════════════════════════════════════════════════════════

function AssignmentRow({ assignment: a, user, isManager, doAction, onAssign, teamNames, isPast }) {
  const [showNames, setShowNames] = useState(false)
  const [menuPos, setMenuPos] = useState(null)
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const baseRole = a.role.replace(/\s+\d+$/, '')
  const icon = ROLE_ICONS[a.role] || ROLE_ICONS[baseRole] || '\uD83D\uDC64'
  const worker = a.cover || a.person
  const isMe = a.person === user || a.cover === user
  const isUnassigned = a.person === 'Select Helper' || a.person === 'TBD'
  const isConfirmed = a.status === 'confirmed'
  const nameOptions = [...new Set([...(isUnassigned ? [] : [a.person]), ...teamNames].filter(Boolean))]
  const chooseName = (name) => {
    onAssign(a.id, name)
    setShowNames(false)
  }

  const computePos = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    const menuWidth = Math.min(220, window.innerWidth - 24)
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - menuWidth - 12))
    setMenuPos({ top: rect.bottom + 4, left, width: menuWidth })
  }, [])

  const openMenu = () => {
    if (showNames) {
      setShowNames(false)
      return
    }
    computePos()
    setShowNames(true)
  }

  useEffect(() => {
    if (!showNames) return
    const handlePointer = (e) => {
      if (triggerRef.current?.contains(e.target)) return
      if (menuRef.current?.contains(e.target)) return
      setShowNames(false)
    }
    const handleKey = (e) => { if (e.key === 'Escape') setShowNames(false) }
    const handleScrollOrResize = () => computePos()
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('touchstart', handlePointer)
    document.addEventListener('keydown', handleKey)
    window.addEventListener('resize', handleScrollOrResize)
    window.addEventListener('scroll', handleScrollOrResize, true)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('touchstart', handlePointer)
      document.removeEventListener('keydown', handleKey)
      window.removeEventListener('resize', handleScrollOrResize)
      window.removeEventListener('scroll', handleScrollOrResize, true)
    }
  }, [showNames, computePos])

  const roleClass = `role-${baseRole.replace(/\s+/g, '-')}`
  const personDisplay = isUnassigned ? (
    'Unassigned'
  ) : a.cover ? (
    <>
      <span className="person-original covered">{a.person}</span>
      <span className="cover-arrow" aria-hidden="true"> → </span>
      <span className={isConfirmed ? 'person-confirmed' : ''}>{a.cover}</span>
    </>
  ) : (
    <span className={isConfirmed ? 'person-confirmed' : ''}>{worker}</span>
  )
  return (
    <div className={`assignment-row ${a.status === 'swap_needed' ? 'swap-needed' : ''} ${isMe ? 'is-me' : ''}`}>
      <div className="assignment-left">
        <span className={`role-icon ${roleClass}`} aria-hidden="true">{icon}</span>
        {isManager && !isPast ? (
          <div className="name-picker">
            <button
              ref={triggerRef}
              type="button"
              className={`person-name-btn ${isUnassigned ? 'unassigned' : ''}`}
              onClick={openMenu}
              aria-label={`Change ${a.role} assignee (currently ${isUnassigned ? 'unassigned' : worker})`}
              aria-expanded={showNames}
              aria-haspopup="listbox"
            >
              {personDisplay}
              {a.swapped_with && <span className="swap-tag"> (swapped with {a.swapped_with})</span>}
            </button>
            {showNames && menuPos && createPortal(
              <div
                ref={menuRef}
                className="name-menu"
                style={{ top: menuPos.top, left: menuPos.left, width: menuPos.width }}
              >
                {isUnassigned && (
                  <button type="button" onClick={() => chooseName('Select Helper')}>Unassigned</button>
                )}
                {nameOptions.map(n => (
                  <button
                    key={n}
                    type="button"
                    className={n === a.person ? 'active' : ''}
                    onClick={() => chooseName(n)}
                  >
                    {n === 'Select Helper' || n === 'TBD' ? 'Unassigned' : n}
                  </button>
                ))}
              </div>,
              document.body
            )}
          </div>
        ) : (
          <span className={`person-name ${isUnassigned ? 'unassigned' : ''}`}>
            {personDisplay}
            {a.swapped_with && <span className="swap-tag"> (swapped with {a.swapped_with})</span>}
          </span>
        )}
      </div>
      <div className="assignment-right">
        {!isPast && (
          <>
            {user && isUnassigned && !isManager && (
              <button className="action-btn volunteer" onClick={() => doAction('volunteer', a.id)}>
                Volunteer
              </button>
            )}

            {a.status === 'pending' && (isMe || isManager) && !isUnassigned && (
              <>
                <button className="action-btn confirm" onClick={() => doAction('confirm', a.id)}>
                  {'\u2713'}
                </button>
                <button className="action-btn decline" onClick={() => doAction('decline', a.id)}>
                  {'\u2717'}
                </button>
              </>
            )}

            {a.status === 'confirmed' && (isMe || isManager) && !isUnassigned && (
              <div className="status-badge confirmed">{'\u2713'}</div>
            )}

            {user && a.status === 'swap_needed' && !isMe && !isUnassigned && (
              <button className="action-btn pickup" onClick={() => doAction('pickup', a.id)}>
                Pick Up
              </button>
            )}

            {a.status === 'swap_needed' && isMe && (
              <button className="action-btn undo" onClick={() => doAction('undo', a.id)}>
                Undo
              </button>
            )}
          </>
        )}

        {isPast && !isUnassigned && (
          <div className={`status-dot ${a.status}`} title={STATUS_LABELS[a.status]} />
        )}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Leaderboard Tab
// ═══════════════════════════════════════════════════════════════

function LeaderboardTab({ stats }) {
  const [period, setPeriod] = useState('All Time')
  const periods = Object.keys(stats)

  const data = stats[period] || {}
  const sorted = Object.entries(data)
    .map(([name, d]) => ({ name, total: d.total, sunday: d.sunday, friday: d.friday }))
    .filter(d => d.total > 0)
    .sort((a, b) => b.total - a.total)

  const medals = ['\uD83E\uDD47', '\uD83E\uDD48', '\uD83E\uDD49']

  return (
    <div className="leaderboard-tab">
      <select
        value={period}
        onChange={e => setPeriod(e.target.value)}
        className="period-select"
      >
        {periods.map(p => <option key={p} value={p}>{p}</option>)}
      </select>

      <div className="lb-list">
        {sorted.map((item, i) => (
          <div key={item.name} className={`lb-row ${i < 3 ? 'top-' + (i + 1) : ''}`}>
            <div className="lb-rank">
              {i < 3 ? medals[i] : `${i + 1}.`}
            </div>
            <div className="lb-name">{item.name}</div>
            <div className="lb-stats">
              <span className="lb-stat" title="Sundays">{'\u26EA'} {item.sunday}</span>
              <span className="lb-stat" title="Fridays">{'\uD83D\uDCD6'} {item.friday}</span>
              <span className="lb-total">{item.total}</span>
            </div>
          </div>
        ))}
        {sorted.length === 0 && (
          <div className="empty-state">No data for this period.</div>
        )}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Team Tab
// ═══════════════════════════════════════════════════════════════

function TeamTab({ team, isManager, loadTeam, showFlash }) {
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [newSundayRoles, setNewSundayRoles] = useState(['Computer', 'Camera 1', 'Camera 2'])
  const [newFridayRoles, setNewFridayRoles] = useState(['Computer', 'Camera'])
  const [newRolePreferences, setNewRolePreferences] = useState({})

  const ALL_SUNDAY_ROLES = ['Computer', 'Camera 1', 'Camera 2']
  const ALL_FRIDAY_ROLES = ['Computer', 'Camera']
  const preferenceOptions = [
    { value: 'less', label: 'Less' },
    { value: 'normal', label: 'Medium' },
    { value: 'more', label: 'More' },
  ]

  const toggleRole = (role, list, setter) => {
    if (list.includes(role)) {
      setter(list.filter(r => r !== role))
    } else {
      setter([...list, role])
    }
  }

  const preferenceKey = (dayType, role) => `${dayType}:${role}`

  const setPreference = (dayType, role, value) => {
    setNewRolePreferences(prev => ({
      ...prev,
      [preferenceKey(dayType, role)]: value,
    }))
  }

  const handleAdd = async () => {
    if (!newName.trim()) return
    try {
      await api('/team', {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          sunday_roles: newSundayRoles,
          friday_roles: newFridayRoles,
          role_preferences: newRolePreferences,
        }),
      })
      showFlash(`${newName} added to team!`)
      setNewName('')
      setNewSundayRoles(['Computer', 'Camera 1', 'Camera 2'])
      setNewFridayRoles(['Computer', 'Camera'])
      setNewRolePreferences({})
      setShowAdd(false)
      loadTeam()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleRemove = async (id, name) => {
    if (!confirm(`Remove ${name} from the team?`)) return
    try {
      await api(`/team/${id}`, { method: 'DELETE' })
      showFlash(`${name} removed`)
      loadTeam()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleToggleActive = async (member) => {
    try {
      await api(`/team/${member.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ active: !member.active }),
      })
      loadTeam()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  return (
    <div className="team-tab">
      <div className="team-header">
        <h2>Team Members</h2>
        {isManager && (
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(!showAdd)}>
            {showAdd ? 'Cancel' : '+ Add Member'}
          </button>
        )}
      </div>

      {/* Add member form */}
      {showAdd && (
        <div className="add-member-form">
          <input
            type="text"
            placeholder="Name"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            className="input-text"
            autoFocus
          />
          <div className="role-toggles">
            <label className="role-group-label">Sunday Roles:</label>
            {ALL_SUNDAY_ROLES.map(role => (
              <div key={role} className="role-toggle">
                <label>
                  <input
                    type="checkbox"
                    checked={newSundayRoles.includes(role)}
                    onChange={() => toggleRole(role, newSundayRoles, setNewSundayRoles)}
                  />
                  {ROLE_ICONS[role]} {role}
                </label>
                {newSundayRoles.includes(role) && (
                  <select
                    value={newRolePreferences[preferenceKey('Sunday', role)] || 'normal'}
                    onChange={e => setPreference('Sunday', role, e.target.value)}
                    className="assign-select"
                  >
                    {preferenceOptions.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                )}
              </div>
            ))}
          </div>
          <div className="role-toggles">
            <label className="role-group-label">Friday Roles:</label>
            {ALL_FRIDAY_ROLES.map(role => (
              <div key={role} className="role-toggle">
                <label>
                  <input
                    type="checkbox"
                    checked={newFridayRoles.includes(role)}
                    onChange={() => toggleRole(role, newFridayRoles, setNewFridayRoles)}
                  />
                  {ROLE_ICONS[role]} {role}
                </label>
                {newFridayRoles.includes(role) && (
                  <select
                    value={newRolePreferences[preferenceKey('Friday', role)] || 'normal'}
                    onChange={e => setPreference('Friday', role, e.target.value)}
                    className="assign-select"
                  >
                    {preferenceOptions.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                )}
              </div>
            ))}
          </div>
          <button className="btn btn-primary" onClick={handleAdd}>Add to Team</button>
        </div>
      )}

      {/* Team list */}
      <div className="team-list">
        {team.map(member => (
          <div key={member.name} className={`team-card ${!member.active ? 'inactive' : ''}`}>
            <div className="team-card-left">
              <div className="team-avatar">{member.name[0]}</div>
              <div className="team-info">
                <span className="team-name">{member.name}</span>
                <div className="team-roles">
                  {(member.sunday_roles || []).map(r => (
                    <span key={r} className="role-chip">{ROLE_ICONS[r]} {r}</span>
                  ))}
                  {(member.friday_roles || []).map(r => (
                    <span key={r} className="role-chip friday">{ROLE_ICONS[r]} {r}</span>
                  ))}
                </div>
              </div>
            </div>
            {isManager && member.id && (
              <div className="team-card-actions">
                <button
                  className={`icon-btn-sm ${member.active ? '' : 'inactive'}`}
                  onClick={() => handleToggleActive(member)}
                  title={member.active ? 'Deactivate' : 'Activate'}
                >
                  {member.active ? '\u2705' : '\u274C'}
                </button>
                <button
                  className="icon-btn-sm danger"
                  onClick={() => handleRemove(member.id, member.name)}
                  title="Remove"
                >
                  {'\uD83D\uDDD1'}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Create Event Modal
// ═══════════════════════════════════════════════════════════════

const PRESET_TYPES = [
  { value: 'Sunday', label: 'Sunday Service', defaultRoles: ['Computer', 'Camera 1', 'Camera 2'] },
  { value: 'Friday', label: 'Bible Study', defaultRoles: ['Computer', 'Camera'] },
  { value: 'Custom', label: 'Custom', defaultRoles: ['Computer', 'Camera'] },
]

function CreateEventModal({ team, onClose, onCreated, showFlash }) {
  const today = new Date()
  const [date, setDate] = useState(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`)
  const [type, setType] = useState('Sunday')
  const [customTitle, setCustomTitle] = useState('')
  const preset = PRESET_TYPES.find(p => p.value === type) || PRESET_TYPES[0]
  const [computerCount, setComputerCount] = useState(preset.defaultRoles.filter(r => r === 'Computer').length || 1)
  const [cameraCount, setCameraCount] = useState(preset.defaultRoles.filter(r => r.startsWith('Camera')).length || 1)
  const [helperCount, setHelperCount] = useState(0)
  const [leaderCount, setLeaderCount] = useState(type === 'Friday' ? 1 : 0)
  const [people, setPeople] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const overlayRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const teamNames = team.length > 0 ? team.map(m => m.name).sort() : []

  // Build the role list from counts
  const roles = []
  for (let i = 0; i < computerCount; i++) roles.push(computerCount > 1 ? `Computer ${i + 1}` : 'Computer')
  if (cameraCount === 1) roles.push('Camera')
  else for (let i = 0; i < cameraCount; i++) roles.push(`Camera ${i + 1}`)
  for (let i = 0; i < leaderCount; i++) roles.push(leaderCount > 1 ? `Leader ${i + 1}` : 'Leader')
  for (let i = 0; i < helperCount; i++) roles.push(helperCount > 1 ? `Helper ${i + 1}` : 'Helper')

  const applyTypeDefaults = (newType) => {
    setType(newType)
    if (newType === 'Sunday') {
      setComputerCount(1)
      setCameraCount(2)
      setLeaderCount(0)
      setHelperCount(0)
    } else if (newType === 'Friday') {
      setComputerCount(1)
      setCameraCount(1)
      setLeaderCount(1)
      setHelperCount(0)
    } else {
      setComputerCount(1)
      setCameraCount(1)
      setLeaderCount(0)
      setHelperCount(0)
    }
  }

  const submit = async () => {
    if (!date) { showFlash('Pick a date', 'error'); return }
    if (type === 'Custom' && !customTitle.trim()) { showFlash('Enter a title', 'error'); return }
    if (roles.length === 0) { showFlash('Add at least one role', 'error'); return }

    setSubmitting(true)
    try {
      const created = await api('/event', {
        method: 'POST',
        body: JSON.stringify({
          date,
          day_type: type,
          custom_title: type === 'Custom' ? customTitle.trim() : null,
          roles,
        }),
      })
      // Assign people if selected
      const assignments = created.assignments || []
      await Promise.all(
        assignments.map(a => {
          const chosen = people[a.role]
          if (!chosen) return Promise.resolve()
          return api(`/assignment/${a.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ person: chosen }),
          }).catch(() => {})
        })
      )
      onCreated()
    } catch (e) {
      showFlash(e.message, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="modal-overlay"
      onMouseDown={(e) => { if (e.target === overlayRef.current) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Create event"
    >
      <div className="modal-card">
        <div className="modal-header">
          <h2>New Event</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>

        <div className="modal-body">
          <label className="modal-field">
            <span className="modal-label">Date</span>
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="modal-input"
            />
          </label>

          <div className="modal-field">
            <span className="modal-label">Type</span>
            <div className="segmented">
              {PRESET_TYPES.map(p => (
                <button
                  key={p.value}
                  type="button"
                  className={`segment ${type === p.value ? 'active' : ''}`}
                  onClick={() => applyTypeDefaults(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {type === 'Custom' && (
            <label className="modal-field">
              <span className="modal-label">Title</span>
              <input
                type="text"
                value={customTitle}
                onChange={e => setCustomTitle(e.target.value)}
                placeholder="Event title"
                className="modal-input"
                autoFocus
              />
            </label>
          )}

          <div className="modal-field">
            <span className="modal-label">Roles</span>
            <div className="counter-grid">
              <Counter label="Computer" value={computerCount} onChange={setComputerCount} max={3} />
              <Counter label="Camera" value={cameraCount} onChange={setCameraCount} max={4} />
              <Counter label="Leader" value={leaderCount} onChange={setLeaderCount} max={2} />
              <Counter label="Helper" value={helperCount} onChange={setHelperCount} max={4} />
            </div>
          </div>

          {roles.length > 0 && (
            <div className="modal-field">
              <span className="modal-label">Assign people (optional)</span>
              <div className="assign-grid">
                {roles.map(role => (
                  <div key={role} className="assign-row">
                    <span className="assign-role">{role}</span>
                    <select
                      value={people[role] || ''}
                      onChange={e => setPeople({ ...people, [role]: e.target.value })}
                      className="modal-input"
                    >
                      <option value="">Unassigned</option>
                      {teamNames.map(n => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? 'Creating…' : 'Create event'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Counter({ label, value, onChange, max = 5 }) {
  return (
    <div className="counter">
      <span className="counter-label">{label}</span>
      <div className="counter-controls">
        <button
          type="button"
          className="counter-btn"
          onClick={() => onChange(Math.max(0, value - 1))}
          disabled={value <= 0}
          aria-label={`Decrease ${label}`}
        >−</button>
        <span className="counter-value">{value}</span>
        <button
          type="button"
          className="counter-btn"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
          aria-label={`Increase ${label}`}
        >+</button>
      </div>
    </div>
  )
}
