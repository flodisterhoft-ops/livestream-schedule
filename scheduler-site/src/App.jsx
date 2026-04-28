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

function TelegramIcon({ size = 16 }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 240 240"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="telegramGradient" x1="0.5" y1="0" x2="0.5" y2="1">
          <stop offset="0%" stopColor="#37AEE2" />
          <stop offset="100%" stopColor="#1E96C8" />
        </linearGradient>
      </defs>
      <circle cx="120" cy="120" r="120" fill="url(#telegramGradient)" />
      <path
        fill="#FFFFFF"
        d="M52 116.7c34.8-15.2 58-25.2 69.6-30.1 33.1-13.8 40-16.2 44.5-16.3 1 0 3.2.2 4.6 1.4 1.2 1 1.5 2.3 1.7 3.3.2 1 .4 3.2.2 4.9-1.8 18.7-9.5 64-13.4 84.9-1.7 8.8-5 11.8-8.2 12.1-7 .6-12.3-4.6-19-9-10.6-7-16.5-11.3-26.7-18.1-11.9-7.8-4.2-12.1 2.6-19.1 1.8-1.8 32.6-29.8 33.2-32.4.1-.3.1-1.5-.6-2.1-.7-.6-1.7-.4-2.5-.2-1.1.2-18 11.5-50.6 33.7-4.8 3.3-9.1 4.9-13 4.8-4.3-.1-12.5-2.4-18.7-4.4-7.5-2.4-13.5-3.7-13-7.9.3-2.1 3.2-4.3 8.7-6.5z"
      />
    </svg>
  )
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
  const [showSuggest, setShowSuggest] = useState(false)
  const [showAdminAddMenu, setShowAdminAddMenu] = useState(false)
  const [showAddMember, setShowAddMember] = useState(false)
  const [showRemoveMember, setShowRemoveMember] = useState(false)
  const [showSchedulingControls, setShowSchedulingControls] = useState(false)
  const [createPrefill, setCreatePrefill] = useState(null)
  const [pendingSuggestId, setPendingSuggestId] = useState(null)

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
    const suggestParam = params.get('suggest')
    if (suggestParam) {
      setPendingSuggestId(suggestParam)
      params.delete('suggest')
      const nextQuery = params.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}${window.location.hash}`)
    }
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
      return d.is_manager
    } catch (e) {
      showFlash(e.message, 'error')
      return false
    }
  }

  const ensureManager = async () => {
    if (isManager) return true
    return toggleManager()
  }

  const openAdminCreateEvent = async () => {
    const ok = await ensureManager()
    if (!ok) return
    setShowAdminAddMenu(false)
    setCreatePrefill(null)
    setShowCreate(true)
  }

  const openAdminAddMember = async () => {
    const ok = await ensureManager()
    if (!ok) return
    setShowAdminAddMenu(false)
    setShowAddMember(true)
  }

  const openAdminRemoveMember = async () => {
    const ok = await ensureManager()
    if (!ok) return
    setShowAdminAddMenu(false)
    setShowRemoveMember(true)
  }

  // When opened from a Telegram suggestion link, fetch & prefill once auth resolved.
  useEffect(() => {
    if (loading || !pendingSuggestId) return
    if (!isAdmin) {
      showFlash('Only the admin can open suggestion requests.', 'error')
      setPendingSuggestId(null)
      return
    }
    let cancelled = false
    const run = async () => {
      try {
        if (!isManager) {
          const ok = await toggleManager()
          if (!ok) return
        }
        const s = await api(`/suggestions/${pendingSuggestId}`)
        if (cancelled) return
        setCreatePrefill(s)
        setShowCreate(true)
        setPendingSuggestId(null)
      } catch (e) {
        if (!cancelled) {
          showFlash(e.message || 'Could not load suggestion', 'error')
          setPendingSuggestId(null)
        }
      }
    }
    run()
    return () => { cancelled = true }
  }, [loading, isAdmin, isManager, pendingSuggestId])

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
      else if (action === 'decline') showFlash("Thanks for letting us know! We'll ask the others to cover your shift.")
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
  const nextEventMonth = schedule
    .filter(e => e.date >= today)
    .sort((a, b) => a.date.localeCompare(b.date))[0]?.date.slice(0, 7)
  const defaultMonth = nextEventMonth && months.includes(nextEventMonth)
    ? nextEventMonth
    : (months.includes(currentMonth) ? currentMonth : months[0])
  const activeMonth = selectedMonth && months.includes(selectedMonth)
    ? selectedMonth
    : defaultMonth
  const pastMonths = new Set(
    months.filter(m => !schedule.some(e => e.date.startsWith(m) && e.date >= today))
  )

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
    <div className={`app ${isManager ? 'manager' : ''}`}>
      {/* Flash message */}
      {flash && (
        <div className={`flash flash-${flash.type}`} role="status" aria-live="polite">{flash.msg}</div>
      )}

      {/* Header */}
      <header className={`app-header ${headerStuck ? 'is-stuck' : ''}`}>
        <div className="header-titles">
          <h1 className="app-title">Livestream Schedule</h1>
          {user && (
            <div className="user-greeting">
              Hi <strong>{user}</strong>
              <span className="wave" role="img" aria-label="waving hand">{'\uD83D\uDC4B'}</span>
            </div>
          )}
        </div>
        <div className="header-actions">
          {isAdmin ? (
            <button
              className="manager-btn"
              onClick={() => setShowAdminAddMenu(true)}
              title="Add"
              aria-label="Add"
            >
              <span className="manager-btn-icon">{'+'}</span>
            </button>
          ) : (
            <button
              className="manager-btn"
              onClick={() => setShowSuggest(true)}
              title="Suggest a date"
              aria-label="Suggest a date"
            >
              <span className="manager-btn-icon">{'+'}</span>
            </button>
          )}
          {isAdmin && (
            <div className="manager-control-stack">
              <button
                className={`manager-btn ${isManager ? 'active' : ''}`}
                onClick={toggleManager}
                title={isManager ? 'Exit Manager Mode' : 'Enter Manager Mode'}
                aria-label={isManager ? 'Exit Manager Mode' : 'Enter Manager Mode'}
                aria-pressed={isManager}
              >
                <span className="manager-btn-icon" key={isManager ? 'on' : 'off'}>
                  {isManager ? '\uD83D\uDEE1\uFE0F' : '\uD83D\uDD13'}
                </span>
              </button>
              {isManager && (
                <button
                  key="schedule-controls"
                  className="schedule-controls-btn"
                  onClick={() => setShowSchedulingControls(true)}
                  title="Scheduling Controls"
                  aria-label="Scheduling Controls"
                >
                  <span className="schedule-controls-icon">{'\u2699\uFE0F'}</span>
                </button>
              )}
            </div>
          )}
          {!user && hasSavedAuth && (
            <button className="icon-btn" onClick={restoreSavedLogin} title="Restore Admin">
              {'\uD83D\uDD12'}
            </button>
          )}
        </div>
      </header>

      <ScheduleTab
        schedule={visibleSchedule}
        months={months}
        pastMonths={pastMonths}
        activeMonth={activeMonth}
        isAdmin={isAdmin}
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
          prefill={createPrefill}
          onClose={() => { setShowCreate(false); setCreatePrefill(null) }}
          onCreated={() => {
            setShowCreate(false)
            setCreatePrefill(null)
            loadSchedule()
            showFlash('Event created')
          }}
          showFlash={showFlash}
        />
      )}
      {showAdminAddMenu && (
        <AdminAddMenu
          onClose={() => setShowAdminAddMenu(false)}
          onAddEvent={openAdminCreateEvent}
          onAddMember={openAdminAddMember}
          onRemoveMember={openAdminRemoveMember}
        />
      )}
      {showAddMember && (
        <AddMemberModal
          onClose={() => setShowAddMember(false)}
          onCreated={() => {
            setShowAddMember(false)
            loadTeam()
          }}
          showFlash={showFlash}
        />
      )}
      {showRemoveMember && (
        <RemoveMemberModal
          team={team}
          onClose={() => setShowRemoveMember(false)}
          onRemoved={(result) => {
            setShowRemoveMember(false)
            loadTeam()
            loadSchedule()
            showFlash(`${result.removed_name} removed from future schedule`)
          }}
          showFlash={showFlash}
        />
      )}
      {showSchedulingControls && (
        <SchedulingControlsModal
          schedule={schedule}
          team={team}
          onClose={() => setShowSchedulingControls(false)}
          onApplied={(result) => {
            setShowSchedulingControls(false)
            loadSchedule()
            showFlash(`Updated ${result.future_assignments_updated} future assignments`)
          }}
          showFlash={showFlash}
        />
      )}
      {showSuggest && (
        <SuggestModal
          defaultName={user || ''}
          onClose={() => setShowSuggest(false)}
          onSubmitted={() => {
            setShowSuggest(false)
            showFlash("Thanks! Your suggestion was sent.")
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

function ScheduleTab({ schedule, months, pastMonths, activeMonth, onMonthChange, user, isAdmin, isManager, doAction, showFlash, loadSchedule, team }) {
  const navRef = useRef(null)
  const [indicator, setIndicator] = useState(null)
  useEffect(() => {
    const measure = () => {
      const nav = navRef.current
      if (!nav) return
      const active = nav.querySelector('.month-pill.active')
      if (!active) { setIndicator(null); return }
      const navRect = nav.getBoundingClientRect()
      const r = active.getBoundingClientRect()
      setIndicator({ x: r.left - navRect.left, y: r.top - navRect.top, w: r.width, h: r.height })
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [activeMonth, months.length, schedule.length])

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
  const yearList = [...new Set(months.map(m => m.slice(0, 4)))].sort()
  const activeYear = (activeMonth && activeMonth.slice(0, 4)) || String(currentYear)
  const isYearPast = (year) => {
    const ms = months.filter(m => m.slice(0, 4) === year)
    return ms.length > 0 && ms.every(m => pastMonths && pastMonths.has(m))
  }
  const handleYearChange = (year) => {
    if (year === activeYear) return
    const yearMonths = months.filter(m => m.slice(0, 4) === year)
    if (yearMonths.length === 0) return
    const firstFuture = yearMonths.find(m => !(pastMonths && pastMonths.has(m)))
    onMonthChange(firstFuture || yearMonths[0])
  }
  const monthsForActiveYear = months.filter(m => m.slice(0, 4) === activeYear)
  const renderMonthPill = (month) => {
    const label = new Date(month + '-15').toLocaleString('en', { month: 'short' })
    const isPast = pastMonths ? pastMonths.has(month) : month < new Date().toISOString().slice(0, 7)
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
      {/* Year navigation */}
      <div className="year-nav">
        {yearList.map(year => (
          <button
            key={year}
            className={`year-pill ${year === activeYear ? 'active' : ''} ${isYearPast(year) ? 'past' : ''}`}
            onClick={() => handleYearChange(year)}
          >
            {year}
          </button>
        ))}
      </div>

      {/* Month navigation */}
      <div className="month-nav" ref={navRef}>
        {indicator && (
          <span
            className="month-indicator"
            style={{
              transform: `translate(${indicator.x}px, ${indicator.y}px)`,
              width: indicator.w,
              height: indicator.h,
            }}
            aria-hidden="true"
          />
        )}
        {monthsForActiveYear.map(renderMonthPill)}
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
            isAdmin={isAdmin}
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

function EventCard({ event, user, isAdmin, isManager, doAction, onNotify, onAssign, onEventUpdate, teamNames }) {
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

  const canEdit = isManager
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
          {isAdmin && !event.is_past && (
            <button
              className={`notify-btn manager-only ${isManager ? 'visible' : ''}`}
              onClick={onNotify}
              title="Send Telegram reminder"
              tabIndex={isManager ? 0 : -1}
              aria-hidden={!isManager}
            >
              <TelegramIcon />
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
    <div className={`assignment-row ${a.status === 'swap_needed' ? 'swap-needed' : ''} ${isConfirmed ? 'confirmed' : ''} ${isMe ? 'is-me' : ''}`}>
      <div className="assignment-left">
        <span className={`role-icon ${roleClass}`} aria-hidden="true">{icon}</span>
        {isManager ? (
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
  const todayKey = toDateKey(new Date())

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
          active_from: todayKey,
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

function AdminAddMenu({ onClose, onAddEvent, onAddMember, onRemoveMember }) {
  const overlayRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      ref={overlayRef}
      className="modal-overlay"
      onMouseDown={(e) => { if (e.target === overlayRef.current) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Add"
    >
      <div className="modal-card action-choice-card">
        <div className="modal-header">
          <h2>What do you want to add?</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>
        <div className="modal-body">
          <button className="action-choice" onClick={onAddEvent}>
            <span className="action-choice-icon">{'\uD83D\uDCC5'}</span>
            <span>
              <strong>Add new event</strong>
              <small>Create a service, Bible study, baptism, thanksgiving, or custom event.</small>
            </span>
          </button>
          <div className="action-choice-split">
            <button className="action-choice compact" onClick={onAddMember}>
              <span className="action-choice-icon">{'\uD83D\uDC64'}</span>
              <span>
                <strong>Add user</strong>
                <small>Add roles and preferences.</small>
              </span>
            </button>
            <button className="action-choice compact danger-choice" onClick={onRemoveMember}>
              <span className="action-choice-icon">{'\uD83D\uDDD1'}</span>
              <span>
                <strong>Remove user</strong>
                <small>Refill future events.</small>
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function AddMemberModal({ onClose, onCreated, showFlash }) {
  const [name, setName] = useState('')
  const [sundayRoles, setSundayRoles] = useState(['Computer', 'Camera 1', 'Camera 2'])
  const [fridayRoles, setFridayRoles] = useState(['Computer', 'Camera'])
  const [rolePreferences, setRolePreferences] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const overlayRef = useRef(null)
  const todayKey = toDateKey(new Date())
  const sundayRoleOptions = ['Computer', 'Camera 1', 'Camera 2']
  const fridayRoleOptions = ['Computer', 'Camera']
  const preferenceOptions = [
    { value: 'less', label: 'Less' },
    { value: 'normal', label: 'Medium' },
    { value: 'more', label: 'More' },
  ]

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const preferenceKey = (dayType, role) => `${dayType}:${role}`

  const setPreference = (dayType, role, value) => {
    setRolePreferences(prev => ({
      ...prev,
      [preferenceKey(dayType, role)]: value,
    }))
  }

  const toggleRole = (role, list, setter) => {
    if (list.includes(role)) setter(list.filter(r => r !== role))
    else setter([...list, role])
  }

  const submit = async () => {
    if (!name.trim()) { showFlash('Enter a name', 'error'); return }
    if (sundayRoles.length === 0 && fridayRoles.length === 0) {
      showFlash('Choose at least one role', 'error')
      return
    }

    setSubmitting(true)
    try {
      await api('/team', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          sunday_roles: sundayRoles,
          friday_roles: fridayRoles,
          role_preferences: rolePreferences,
          active_from: todayKey,
        }),
      })
      showFlash(`${name.trim()} added to team!`)
      onCreated()
    } catch (e) {
      showFlash(e.message, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const renderRoleRow = (dayType, role, selected, toggle) => (
    <div key={`${dayType}:${role}`} className={`member-role-row ${selected ? 'selected' : ''}`}>
      <label>
        <input type="checkbox" checked={selected} onChange={toggle} />
        <span>{ROLE_ICONS[role]} {role}</span>
      </label>
      {selected && (
        <div className="segmented preference-segmented">
          {preferenceOptions.map(opt => (
            <button
              key={opt.value}
              type="button"
              className={`segment ${((rolePreferences[preferenceKey(dayType, role)] || 'normal') === opt.value) ? 'active' : ''}`}
              onClick={() => setPreference(dayType, role, opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <div
      ref={overlayRef}
      className="modal-overlay"
      onMouseDown={(e) => { if (e.target === overlayRef.current) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Add user"
    >
      <div className="modal-card">
        <div className="modal-header">
          <h2>Add New User</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>
        <div className="modal-body">
          <label className="modal-field">
            <span className="modal-label">Name</span>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="New team member"
              className="modal-input"
              autoFocus
            />
          </label>

          <div className="modal-field">
            <span className="modal-label">Sunday roles</span>
            <div className="member-role-list">
              {sundayRoleOptions.map(role => renderRoleRow(
                'Sunday',
                role,
                sundayRoles.includes(role),
                () => toggleRole(role, sundayRoles, setSundayRoles)
              ))}
            </div>
          </div>

          <div className="modal-field">
            <span className="modal-label">Friday roles</span>
            <div className="member-role-list">
              {fridayRoleOptions.map(role => renderRoleRow(
                'Friday',
                role,
                fridayRoles.includes(role),
                () => toggleRole(role, fridayRoles, setFridayRoles)
              ))}
            </div>
          </div>

          <p className="modal-help">
            Less, Medium, and More control how strongly the scheduler prefers this person for each role on future generated schedules.
          </p>
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? 'Adding…' : 'Add user'}
          </button>
        </div>
      </div>
    </div>
  )
}

function RemoveMemberModal({ team, onClose, onRemoved, showFlash }) {
  const [memberId, setMemberId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const overlayRef = useRef(null)
  const selected = team.find(m => String(m.id) === String(memberId))

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async () => {
    if (!selected) { showFlash('Choose a user to remove', 'error'); return }
    setSubmitting(true)
    try {
      const result = await api(`/team/${selected.id}`, { method: 'DELETE' })
      onRemoved(result)
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
      aria-label="Remove user"
    >
      <div className="modal-card">
        <div className="modal-header">
          <h2>Remove User</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>
        <div className="modal-body">
          <label className="modal-field">
            <span className="modal-label">User</span>
            <select value={memberId} onChange={e => setMemberId(e.target.value)} className="modal-input">
              <option value="">Select a person</option>
              {team.filter(m => m.id).map(member => (
                <option key={member.id} value={member.id}>{member.name}</option>
              ))}
            </select>
          </label>
          <p className="modal-help">
            This removes the person from the team and refills only future schedule assignments using the existing fairness rules. Past events stay unchanged.
          </p>
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn btn-danger" onClick={submit} disabled={submitting || !selected}>
            {submitting ? 'Removing…' : 'Remove and refill'}
          </button>
        </div>
      </div>
    </div>
  )
}

const CONTROL_ROLES = [
  { key: 'Sunday:Computer', label: 'Sun Computer', dayType: 'Sunday', roles: ['Computer'] },
  { key: 'Sunday:Camera 1', label: 'Sun Cam 1', dayType: 'Sunday', roles: ['Camera 1'] },
  { key: 'Sunday:Camera 2', label: 'Sun Cam 2', dayType: 'Sunday', roles: ['Camera 2'] },
  { key: 'Friday:Computer', label: 'Bible Computer', dayType: 'Friday', roles: ['Computer'] },
  { key: 'Friday:Camera', label: 'Bible Camera', dayType: 'Friday', roles: ['Camera'] },
]

function SchedulingControlsModal({ schedule, team, onClose, onApplied, showFlash }) {
  const todayKey = toDateKey(new Date())
  const activeTeam = team.filter(member => member.id && member.active !== false)
  const overlayRef = useRef(null)
  const initialTargets = useRef(buildSchedulingTargets(schedule, activeTeam, todayKey))
  const [targets, setTargets] = useState(initialTargets.current)
  const [submitting, setSubmitting] = useState(false)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { confirming ? setConfirming(false) : onClose() } }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, confirming])

  const futureEvents = schedule.filter(event => event.date >= todayKey && ['Sunday', 'Friday'].includes(event.day_type))
  const futureMonths = new Set(futureEvents.map(event => event.date.slice(0, 7)))
  const slotTotals = CONTROL_ROLES.reduce((acc, role) => {
    acc[role.key] = futureEvents.reduce((total, event) => {
      if (event.day_type !== role.dayType) return total
      return total + (event.assignments || []).filter(a => role.roles.includes(a.role)).length
    }, 0)
    return acc
  }, {})
  const totalSlots = CONTROL_ROLES.reduce((sum, role) => sum + (slotTotals[role.key] || 0), 0)

  const getValue = (roleKey, name) => targets[roleKey]?.[name] || 0
  const roleTotal = (roleKey) => Object.values(targets[roleKey] || {}).reduce((sum, value) => sum + value, 0)
  const delta = CONTROL_ROLES.reduce((acc, role) => {
    acc[role.key] = roleTotal(role.key) - (slotTotals[role.key] || 0)
    return acc
  }, {})
  const balanced = CONTROL_ROLES.every(role => delta[role.key] === 0)
  const totalChanges = CONTROL_ROLES.reduce((sum, role) => {
    return sum + (initialTargets.current[role.key]
      ? Object.keys(targets[role.key] || {}).reduce((d, name) => {
          return d + Math.abs((targets[role.key][name] || 0) - (initialTargets.current[role.key][name] || 0))
        }, 0)
      : 0)
  }, 0) / 2

  const isEligible = (member, role) => {
    const list = role.dayType === 'Sunday' ? member.sunday_roles || [] : member.friday_roles || []
    return role.roles.some(r => list.includes(r))
  }

  const updateTarget = (roleKey, name, amount) => {
    setTargets(prev => ({
      ...prev,
      [roleKey]: {
        ...(prev[roleKey] || {}),
        [name]: Math.max(0, (prev[roleKey]?.[name] || 0) + amount),
      },
    }))
  }

  const resetToCurrent = () => {
    setTargets(initialTargets.current)
  }

  const distributeEvenly = () => {
    setTargets(prev => {
      const next = { ...prev }
      CONTROL_ROLES.forEach(role => {
        const eligibleNames = activeTeam.filter(m => isEligible(m, role)).map(m => m.name)
        const total = slotTotals[role.key] || 0
        const baseline = eligibleNames.length ? Math.floor(total / eligibleNames.length) : 0
        const remainder = eligibleNames.length ? total - baseline * eligibleNames.length : 0
        const distribution = eligibleNames.reduce((acc, name, idx) => {
          acc[name] = baseline + (idx < remainder ? 1 : 0)
          return acc
        }, {})
        const fullRow = (next[role.key] && Object.keys(next[role.key])) || []
        fullRow.forEach(name => {
          if (!(name in distribution)) distribution[name] = 0
        })
        next[role.key] = distribution
      })
      return next
    })
  }

  const handleApply = async () => {
    if (!balanced) { showFlash('Balance every role before saving', 'error'); return }
    setConfirming(true)
  }

  const performApply = async () => {
    setSubmitting(true)
    try {
      const result = await api('/scheduling-controls/apply', {
        method: 'POST',
        body: JSON.stringify({ targets }),
      })
      onApplied(result)
    } catch (e) {
      showFlash(e.message, 'error')
    } finally {
      setSubmitting(false)
      setConfirming(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="modal-overlay"
      onMouseDown={(e) => { if (e.target === overlayRef.current) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Scheduling Controls"
    >
      <div className="modal-card scheduling-card">
        <div className="modal-header">
          <div>
            <h2>Scheduling Controls</h2>
            <p className="modal-subtitle">
              Edits balance and apply only to {totalSlots} future regular slots across {futureMonths.size} month{futureMonths.size === 1 ? '' : 's'}. Past events stay untouched.
            </p>
          </div>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>

        <div className="modal-body">
          <div className="control-toolbar">
            <button className="btn btn-ghost btn-sm" onClick={resetToCurrent} type="button">Reset to current</button>
            <button className="btn btn-outline btn-sm" onClick={distributeEvenly} type="button">Distribute evenly</button>
            <span className={`balance-status ${balanced ? 'balanced' : 'unbalanced'}`}>
              {balanced ? 'Balanced' : `${CONTROL_ROLES.filter(r => delta[r.key] !== 0).length} role${CONTROL_ROLES.filter(r => delta[r.key] !== 0).length === 1 ? '' : 's'} need attention`}
            </span>
          </div>

          <div className="schedule-control-table-wrap">
            <table className="schedule-control-table">
              <thead>
                <tr>
                  <th>Person</th>
                  {CONTROL_ROLES.map(role => (
                    <th key={role.key}>
                      <div className="control-th-stack">
                        <span>{role.label}</span>
                        <span className={`role-delta ${delta[role.key] === 0 ? 'ok' : delta[role.key] > 0 ? 'over' : 'under'}`}>
                          {delta[role.key] === 0
                            ? `${slotTotals[role.key] || 0}`
                            : `${roleTotal(role.key)} / ${slotTotals[role.key] || 0}`}
                          {delta[role.key] !== 0 && (
                            <em>{delta[role.key] > 0 ? `Remove ${delta[role.key]}` : `Add ${Math.abs(delta[role.key])}`}</em>
                          )}
                        </span>
                      </div>
                    </th>
                  ))}
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {activeTeam.map(member => {
                  const personTotal = CONTROL_ROLES.reduce((sum, role) => sum + getValue(role.key, member.name), 0)
                  return (
                    <tr key={member.id}>
                      <td>
                        <div className="person-cell">
                          <span className="team-avatar small">{member.name[0]}</span>
                          <span>{member.name}</span>
                        </div>
                      </td>
                      {CONTROL_ROLES.map(role => {
                        const eligible = isEligible(member, role)
                        const val = getValue(role.key, member.name)
                        return (
                          <td key={role.key}>
                            {eligible ? (
                              <div className={`target-control ${val > 0 ? 'has-value' : ''}`}>
                                <button type="button" onClick={() => updateTarget(role.key, member.name, -1)} disabled={val <= 0} aria-label={`Decrease ${role.label}`}>-</button>
                                <span>{val}</span>
                                <button type="button" onClick={() => updateTarget(role.key, member.name, 1)} disabled={val >= (slotTotals[role.key] || 0)} aria-label={`Increase ${role.label}`}>+</button>
                              </div>
                            ) : (
                              <span className="not-eligible" title="Not eligible">-</span>
                            )}
                          </td>
                        )
                      })}
                      <td className="row-total">{personTotal}</td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td>Totals</td>
                  {CONTROL_ROLES.map(role => (
                    <td key={role.key} className={delta[role.key] === 0 ? 'balanced-total' : 'unbalanced-total'}>
                      {roleTotal(role.key)} / {slotTotals[role.key] || 0}
                    </td>
                  ))}
                  <td>{CONTROL_ROLES.reduce((sum, role) => sum + roleTotal(role.key), 0)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>

        <div className="modal-footer">
          <span className="footer-hint">{totalChanges > 0 ? `${totalChanges} change${totalChanges === 1 ? '' : 's'}` : 'No changes yet'}</span>
          <div className="footer-actions">
            <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
            <button className="btn btn-primary" onClick={handleApply} disabled={submitting || !balanced || totalChanges === 0}>
              {submitting ? 'Applying…' : 'Apply to future schedule'}
            </button>
          </div>
        </div>
      </div>

      {confirming && (
        <ConfirmApplyModal
          totalSlots={totalSlots}
          totalChanges={totalChanges}
          months={futureMonths.size}
          submitting={submitting}
          onCancel={() => setConfirming(false)}
          onConfirm={performApply}
        />
      )}
    </div>
  )
}

function ConfirmApplyModal({ totalSlots, totalChanges, months, submitting, onCancel, onConfirm }) {
  return (
    <div className="modal-overlay confirm-overlay" role="dialog" aria-modal="true" aria-label="Confirm apply">
      <div className="modal-card confirm-card">
        <div className="modal-header">
          <h2>Apply targets?</h2>
        </div>
        <div className="modal-body">
          <p className="modal-help">
            This rewrites <strong>{totalSlots}</strong> future regular slots across <strong>{months}</strong> month{months === 1 ? '' : 's'} based on your target distribution.
          </p>
          <ul className="modal-list">
            <li>{totalChanges} planned change{totalChanges === 1 ? '' : 's'} from current distribution.</li>
            <li>Past events are untouched.</li>
            <li>Custom events (baptisms, etc.) stay unchanged.</li>
            <li>Florian gap and cap rules still apply when possible.</li>
          </ul>
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onCancel} disabled={submitting}>Back</button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={submitting}>
            {submitting ? 'Applying…' : 'Yes, apply'}
          </button>
        </div>
      </div>
    </div>
  )
}

function buildSchedulingTargets(schedule, team, todayKey) {
  const names = team.map(member => member.name)
  const initial = CONTROL_ROLES.reduce((acc, role) => {
    acc[role.key] = names.reduce((people, name) => {
      people[name] = 0
      return people
    }, {})
    return acc
  }, {})

  schedule
    .filter(event => event.date >= todayKey && ['Sunday', 'Friday'].includes(event.day_type))
    .forEach(event => {
      ;(event.assignments || []).forEach(assignment => {
        const match = CONTROL_ROLES.find(role => role.dayType === event.day_type && role.roles.includes(assignment.role))
        const worker = assignment.cover || assignment.person
        if (match && worker && initial[match.key] && worker in initial[match.key]) {
          initial[match.key][worker] += 1
        }
      })
    })

  return initial
}

// ═══════════════════════════════════════════════════════════════
//  Create Event Modal
// ═══════════════════════════════════════════════════════════════

const PRESET_TYPES = [
  { value: 'Sunday', label: 'Sunday Service', defaultRoles: ['Computer', 'Camera 1', 'Camera 2'] },
  { value: 'Friday', label: 'Bible Study', defaultRoles: ['Computer', 'Camera'] },
  { value: 'Custom', label: 'Custom', defaultRoles: ['Computer', 'Camera'] },
]

function CreateEventModal({ team, prefill, onClose, onCreated, showFlash }) {
  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const initialDate = prefill?.date || todayKey
  const initialType = 'Custom'
  const suggestionTitle = (() => {
    if (!prefill) return ''
    if (prefill.event_type === 'Other') return prefill.custom_title || ''
    const parts = [prefill.event_type]
    if (prefill.time) parts.push(`at ${prefill.time}`)
    return parts.join(' ')
  })()
  const [date, setDate] = useState(initialDate)
  const [type, setType] = useState(prefill ? initialType : 'Sunday')
  const [customTitle, setCustomTitle] = useState(prefill ? suggestionTitle : '')
  const preset = PRESET_TYPES.find(p => p.value === type) || PRESET_TYPES[0]
  const [computerCount, setComputerCount] = useState(preset.defaultRoles.filter(r => r === 'Computer').length || 1)
  const [cameraCount, setCameraCount] = useState(preset.defaultRoles.filter(r => r.startsWith('Camera')).length || 1)
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

  const applyTypeDefaults = (newType) => {
    setType(newType)
    if (newType === 'Sunday') {
      setComputerCount(1)
      setCameraCount(2)
    } else if (newType === 'Friday') {
      setComputerCount(1)
      setCameraCount(1)
    } else {
      setComputerCount(1)
      setCameraCount(1)
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
          suggestion_id: prefill?.id,
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
          <h2>{prefill ? 'Review Suggestion' : 'New Event'}</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>

        <div className="modal-body">
          {prefill && (
            <div className="suggestion-banner">
              <div><strong>{prefill.suggester_name}</strong> suggested:</div>
              <div className="suggestion-banner-line">
                {prefill.event_type === 'Other'
                  ? (prefill.custom_title || 'Custom event')
                  : prefill.event_type}
                {prefill.time ? ` · ${prefill.time}` : ''}
              </div>
              {prefill.notes && <div className="suggestion-banner-notes">{prefill.notes}</div>}
            </div>
          )}
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

// ═══════════════════════════════════════════════════════════════
//  Suggest Event Modal (open to everyone)
// ═══════════════════════════════════════════════════════════════

const SUGGEST_TYPES = [
  'Baptism',
  'Thanksgiving',
  'Samaritan Aid Mission Conference',
  'Other',
]

function SuggestModal({ defaultName, onClose, onSubmitted, showFlash }) {
  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const [name, setName] = useState(defaultName || '')
  const [eventType, setEventType] = useState(SUGGEST_TYPES[0])
  const [customTitle, setCustomTitle] = useState('')
  const [date, setDate] = useState(todayKey)
  const [time, setTime] = useState('')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const overlayRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async () => {
    if (!name.trim()) { showFlash('Please enter your name', 'error'); return }
    if (eventType === 'Other' && !customTitle.trim()) { showFlash('Please enter a title', 'error'); return }
    if (!date) { showFlash('Please pick a date', 'error'); return }
    setSubmitting(true)
    try {
      await api('/suggestions', {
        method: 'POST',
        body: JSON.stringify({
          suggester_name: name.trim(),
          event_type: eventType,
          custom_title: eventType === 'Other' ? customTitle.trim() : null,
          date,
          time: time || null,
          notes: notes.trim() || null,
        }),
      })
      onSubmitted()
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
      aria-label="Suggest a date"
    >
      <div className="modal-card">
        <div className="modal-header">
          <h2>Suggest a Date</h2>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>

        <div className="modal-body">
          <p className="modal-help">
            Suggest a livestream event. The admin will receive a Telegram notification and can turn it into a scheduled event.
          </p>

          <label className="modal-field">
            <span className="modal-label">Your name</span>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              className="modal-input"
              placeholder="Enter your name"
              autoFocus={!defaultName}
            />
          </label>

          <div className="modal-field">
            <span className="modal-label">Type of event</span>
            <div className="segmented segmented-stack">
              {SUGGEST_TYPES.map(t => (
                <button
                  key={t}
                  type="button"
                  className={`segment ${eventType === t ? 'active' : ''}`}
                  onClick={() => setEventType(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {eventType === 'Other' && (
            <label className="modal-field">
              <span className="modal-label">Title</span>
              <input
                type="text"
                value={customTitle}
                onChange={e => setCustomTitle(e.target.value)}
                placeholder="What's the event called?"
                className="modal-input"
              />
            </label>
          )}

          <div className="modal-field-row">
            <label className="modal-field">
              <span className="modal-label">Date</span>
              <input
                type="date"
                value={date}
                min={todayKey}
                onChange={e => setDate(e.target.value)}
                className="modal-input"
              />
            </label>
            <label className="modal-field">
              <span className="modal-label">Time</span>
              <input
                type="time"
                value={time}
                onChange={e => setTime(e.target.value)}
                className="modal-input"
              />
            </label>
          </div>

          <label className="modal-field">
            <span className="modal-label">Notes (optional)</span>
            <textarea
              value={notes}
              onChange={e => setNotes(e.target.value)}
              className="modal-input"
              rows={3}
              placeholder="Anything the admin should know"
            />
          </label>
        </div>

        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? 'Sending…' : 'Submit suggestion'}
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
