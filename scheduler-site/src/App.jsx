import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'

const API = '/api/v2'
const AUTH_TOKEN_KEY = 'livestreamV2AuthToken'
const TELEGRAM_LOGIN_KEYS = ['id', 'first_name', 'last_name', 'username', 'photo_url', 'auth_date', 'hash']

const ROLE_ICONS = {
  Computer: '\uD83D\uDCBB',
  'Camera 1': '\uD83D\uDCF9',
  'Camera 2': '\uD83D\uDCF9',
  Camera: '\uD83D\uDCF9',
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

const timeAgo = (input) => {
  if (!input) return ''
  const then = new Date(input).getTime()
  if (Number.isNaN(then)) return ''
  const diffSeconds = Math.floor((Date.now() - then) / 1000)
  if (diffSeconds < 5) return 'just now'
  if (diffSeconds < 60) return `${diffSeconds}s ago`
  const minutes = Math.floor(diffSeconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks}w ago`
  return new Date(input).toLocaleDateString()
}

const defaultStartTime = (type) => type === 'Sunday' ? '14:00' : '19:00'

const formatDisplayTime = (value) => {
  if (!value) return ''
  const [hourRaw, minuteRaw] = value.split(':')
  const hour = Number(hourRaw)
  if (Number.isNaN(hour)) return value
  const minute = minuteRaw || '00'
  const suffix = hour >= 12 ? 'PM' : 'AM'
  const displayHour = hour % 12 || 12
  return `${displayHour}:${minute} ${suffix}`
}

const normalizeSuggestedTime = (value) => {
  if (!value) return ''
  const trimmed = value.trim()
  const direct = trimmed.match(/^([01]?\d|2[0-3]):([0-5]\d)$/)
  if (direct) return `${direct[1].padStart(2, '0')}:${direct[2]}`
  const match = trimmed.match(/^(\d{1,2})(?::([0-5]\d))?\s*(am|pm)$/i)
  if (!match) return ''
  let hour = Number(match[1])
  const minute = match[2] || '00'
  const period = match[3].toLowerCase()
  if (period === 'pm' && hour < 12) hour += 12
  if (period === 'am' && hour === 12) hour = 0
  if (hour < 0 || hour > 23) return ''
  return `${String(hour).padStart(2, '0')}:${minute}`
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
  const [showCreate, setShowCreate] = useState(false)
  const [showSuggest, setShowSuggest] = useState(false)
  const [showAdminAddMenu, setShowAdminAddMenu] = useState(false)
  const [showRoleSettings, setShowRoleSettings] = useState(false)
  const [showYearOverview, setShowYearOverview] = useState(false)
  const [createPrefill, setCreatePrefill] = useState(null)
  const [pendingSuggestId, setPendingSuggestId] = useState(null)
  const [recentlyChanged, setRecentlyChanged] = useState(() => new Set())


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
      localStorage.removeItem(AUTH_TOKEN_KEY)
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

  const openAdminCreateEvent = () => {
    setShowAdminAddMenu(false)
    setCreatePrefill(null)
    setShowCreate(true)
  }

  const openRoleSettings = () => {
    setShowAdminAddMenu(false)
    setShowRoleSettings(true)
  }

  const openYearOverview = () => {
    setShowAdminAddMenu(false)
    setShowYearOverview(true)
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
          showFlash('Turn on manager mode first to open suggestion requests.', 'error')
          setPendingSuggestId(null)
          return
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
  const currentMonth = toMonthKey(now)
  const months = [...new Set(schedule.map(e => e.date.slice(0, 7)))]
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
  const visibleSchedule = filtered.sort((a, b) => a.date.localeCompare(b.date))

  return (
    <div className={`app ${isManager ? 'manager' : ''}`}>
      {/* Flash message */}
      {flash && (
        <div className={`flash flash-${flash.type || 'success'}`} role="status" aria-live="polite">
          <span>{flash.msg}</span>
          {flash.undoSnapshotId && (
            <button
              className="flash-undo"
              type="button"
              onClick={async () => {
                const snapshotId = flash.undoSnapshotId
                setFlash(null)
                try {
                  const res = await api('/scheduling-controls/undo', {
                    method: 'POST',
                    body: JSON.stringify({ snapshot_id: snapshotId }),
                  })
                  loadSchedule()
                  showFlash(`Undid last apply (${res.restored} assignments restored)`)
                } catch (e) {
                  showFlash(e.message, 'error')
                }
              }}
            >
              Undo
            </button>
          )}
        </div>
      )}

      {/* Header */}
      <header className="app-header">
        <div className="header-titles">
          <h1 className="app-title">Livestream Schedule</h1>
          {user && (
            <div className="user-greeting">
              Hi <strong>{user}</strong>
              <span className="wave" role="img" aria-label="waving hand">{'\uD83D\uDC4B'}</span>
            </div>
          )}
        </div>
        <div className={`header-actions ${isAdmin ? 'admin-actions' : ''}`}>
          {isAdmin ? (
            <>
              <button
                className="manager-btn add-btn"
                onClick={openAdminCreateEvent}
                title="Add new event"
                aria-label="Add new event"
              >
                <span className="manager-btn-icon">{'+'}</span>
              </button>
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
              <button
                className="manager-btn settings-btn"
                onClick={openRoleSettings}
                title="Scheduling settings"
                aria-label="Scheduling settings"
              >
                <span className="manager-btn-icon">{'\u2699\uFE0F'}</span>
              </button>
              <button
                className="manager-btn overview-btn"
                onClick={openYearOverview}
                title="Year overview"
                aria-label="Year overview"
              >
                <span className="manager-btn-icon">{'\uD83D\uDCCA'}</span>
              </button>
            </>
          ) : (
            <>
              <button
                className="manager-btn add-btn"
                onClick={() => setShowSuggest(true)}
                title="Suggest a date"
                aria-label="Suggest a date"
              >
                <span className="manager-btn-icon">{'+'}</span>
              </button>
              <button
                className="manager-btn overview-btn"
                onClick={openYearOverview}
                title="Year overview"
                aria-label="Year overview"
              >
                <span className="manager-btn-icon">{'\uD83D\uDCCA'}</span>
              </button>
            </>
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
        recentlyChanged={recentlyChanged}
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
      {showRoleSettings && (
        <RoleSettingsModal
          team={team}
          onClose={() => setShowRoleSettings(false)}
          onSaved={(result) => {
            loadTeam()
            loadSchedule()
            const changed = result?.future_assignments_replaced || 0
            showFlash(changed ? `Scheduling settings saved — ${changed} future assignments updated` : 'Scheduling settings saved')
          }}
          showFlash={showFlash}
        />
      )}
      {showYearOverview && (
        <YearOverviewModal
          schedule={schedule}
          team={team}
          onClose={() => setShowYearOverview(false)}
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

function ScheduleTab({ schedule, months, pastMonths, activeMonth, onMonthChange, user, isAdmin, isManager, doAction, showFlash, loadSchedule, team, recentlyChanged }) {
  const navRef = useRef(null)
  const filterRef = useRef(null)
  const [indicator, setIndicator] = useState(null)
  const [selectedPerson, setSelectedPerson] = useState('')
  const [filterOpen, setFilterOpen] = useState(false)
  const [viewMode, setViewMode] = useState('cards')
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

  const handleToggleLock = async (assignmentId, nextLocked) => {
    try {
      await api(`/assignment/${assignmentId}/lock`, {
        method: 'POST',
        body: JSON.stringify({ locked: nextLocked }),
      })
      loadSchedule()
      showFlash(nextLocked ? 'Assignment locked' : 'Assignment unlocked')
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const teamNames = team.length > 0
    ? team.map(m => m.name).sort()
    : ['Andy', 'Florian', 'Marvin', 'Patric', 'Rene', 'Stefan', 'Viktor', 'TBD']
  const filterNames = teamNames.filter(n => n && n !== 'TBD' && n !== 'Select Helper')
  const personShiftCounts = useMemo(() => {
    const counts = Object.fromEntries(filterNames.map(name => [name, 0]))
    schedule.forEach(event => {
      event.assignments.forEach(a => {
        const worker = a.cover || a.person
        if (worker && counts[worker] !== undefined) counts[worker] += 1
      })
    })
    return counts
  }, [schedule, filterNames])
  const filteredSchedule = selectedPerson
    ? schedule.filter(event => event.assignments.some(a => a.person === selectedPerson || a.cover === selectedPerson))
    : schedule
  const selectedPersonCount = filteredSchedule.length

  useEffect(() => {
    if (!filterOpen) return
    const handlePointer = (e) => {
      if (filterRef.current?.contains(e.target)) return
      setFilterOpen(false)
    }
    const handleKey = (e) => { if (e.key === 'Escape') setFilterOpen(false) }
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('touchstart', handlePointer)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('touchstart', handlePointer)
      document.removeEventListener('keydown', handleKey)
    }
  }, [filterOpen])

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

      <div className="person-filter" ref={filterRef}>
        <div className={`person-filter-trigger ${selectedPerson ? 'active' : ''} ${viewMode === 'calendar' ? 'calendar-active' : ''}`}>
          <button
            type="button"
            className="person-filter-main"
            onClick={() => setFilterOpen(v => !v)}
            aria-haspopup="listbox"
            aria-expanded={filterOpen}
          >
            <span className="person-filter-icon">{'\uD83D\uDC64'}</span>
            <span className="person-filter-text">
              {selectedPerson ? selectedPerson : 'All team members'}
            </span>
            <span className="person-filter-count">
              {selectedPerson ? `${selectedPersonCount} shift${selectedPersonCount === 1 ? '' : 's'}` : 'Filter'}
            </span>
            <span className={`person-filter-chevron ${filterOpen ? 'open' : ''}`}>{'\u203A'}</span>
          </button>
          <button
            type="button"
            className={`person-calendar-toggle ${viewMode === 'calendar' ? 'active' : ''}`}
            onClick={() => setViewMode(viewMode === 'calendar' ? 'cards' : 'calendar')}
            aria-label={viewMode === 'calendar' ? 'Show schedule cards' : 'Show calendar'}
            aria-pressed={viewMode === 'calendar'}
          >
            {viewMode === 'calendar' ? '\uD83D\uDCCB' : '\uD83D\uDCC5'}
          </button>
        </div>
        {filterOpen && (
          <div className="person-filter-menu" role="listbox" aria-label="Filter schedule by name">
            <button
              type="button"
              className={`person-filter-option ${selectedPerson === '' ? 'selected' : ''}`}
              onClick={() => { setSelectedPerson(''); setFilterOpen(false) }}
              role="option"
              aria-selected={selectedPerson === ''}
            >
              <span>{'\u2728'} All team members</span>
              <span className="person-filter-option-meta">
                {schedule.length} service day{schedule.length === 1 ? '' : 's'}
                {selectedPerson === '' && <span className="person-filter-check">{'\u2713'}</span>}
              </span>
            </button>
            {filterNames.map(name => (
              <button
                key={name}
                type="button"
                className={`person-filter-option ${selectedPerson === name ? 'selected' : ''}`}
                onClick={() => { setSelectedPerson(name); setFilterOpen(false) }}
                role="option"
                aria-selected={selectedPerson === name}
              >
                <span>{name}</span>
                <span className="person-filter-option-meta">
                  {personShiftCounts[name] || 0} shift{personShiftCounts[name] === 1 ? '' : 's'}
                  {selectedPerson === name && <span className="person-filter-check">{'\u2713'}</span>}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {viewMode === 'calendar' ? (
        <MonthCalendar activeMonth={activeMonth} events={filteredSchedule} selectedPerson={selectedPerson} />
      ) : (
        <div className="events-list">
          {filteredSchedule.length === 0 && (
            <div className="empty-state">
              <p>{selectedPerson ? `No shifts for ${selectedPerson} this month.` : 'No events for this month.'}</p>
              {isManager && !selectedPerson && <p>Generate one or use Telegram to add an event.</p>}
            </div>
          )}
          {filteredSchedule.map(event => (
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
              onToggleLock={handleToggleLock}
              teamNames={teamNames}
              recentlyChanged={recentlyChanged}
              selectedPerson={selectedPerson}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function MonthCalendar({ activeMonth, events, selectedPerson }) {
  const todayKey = toDateKey(new Date())
  const eventsByDate = useMemo(() => {
    const map = new Map()
    events.forEach(event => {
      const dateEvents = map.get(event.date) || []
      dateEvents.push(event)
      map.set(event.date, dateEvents)
    })
    return map
  }, [events])
  const days = useMemo(() => {
    if (!activeMonth) return []
    const [year, month] = activeMonth.split('-').map(Number)
    const first = new Date(year, month - 1, 1, 12)
    const last = new Date(year, month, 0, 12)
    const start = new Date(first)
    start.setDate(first.getDate() - ((first.getDay() + 6) % 7))
    const end = new Date(last)
    end.setDate(last.getDate() + (6 - ((last.getDay() + 6) % 7)))
    const result = []
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      result.push(new Date(d))
    }
    return result
  }, [activeMonth])
  const weeks = useMemo(() => {
    const result = []
    for (let i = 0; i < days.length; i += 7) result.push(days.slice(i, i + 7))
    return result
  }, [days])
  const weekdays = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

  return (
    <div className="calendar-view">
      <div className="calendar-weekdays">
        {weekdays.map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
      </div>
      <div className="calendar-grid">
        {weeks.map((week, weekIndex) => (
          <div className="calendar-week-row" key={`week-${weekIndex}`}>
            {week.map(day => {
              const dateKey = toDateKey(day)
              const dayEvents = eventsByDate.get(dateKey) || []
              const isOutside = activeMonth && !dateKey.startsWith(activeMonth)
              const dayClasses = [
                'calendar-day',
                isOutside ? 'outside' : '',
                day.getDay() === 0 ? 'sunday' : '',
                day.getDay() === 6 ? 'saturday' : '',
                dateKey === todayKey ? 'today' : '',
                dayEvents.length ? 'has-event' : '',
              ].filter(Boolean).join(' ')
              return (
                <div key={dateKey} className={dayClasses}>
                  <div className="calendar-day-number">{day.getDate()}</div>
                  <div className="calendar-day-events">
                    {dayEvents.map(event => {
                      const assignments = event.assignments.filter(a => !selectedPerson || a.person === selectedPerson || a.cover === selectedPerson)
                      const eventKind = event.day_type === 'Friday' ? 'friday' : event.day_type === 'Sunday' ? 'sunday' : 'custom'
                      return (
                        <div className={`calendar-event ${event.is_past ? 'past' : ''}`} key={`${event.date}-${event.title}-${event.day_type}`}>
                          <div className={`calendar-chip calendar-event-chip ${eventKind}`} title={event.day_type === 'Friday' ? 'Bible Study' : event.title}>
                            <span className="calendar-chip-text">{event.day_type === 'Friday' ? 'Bible Study' : event.title}</span>
                          </div>
                          {assignments.slice(0, 4).map(a => {
                            const worker = a.cover || a.person
                            const baseRole = a.role.replace(/\s+\d+$/, '')
                            const icon = ROLE_ICONS[a.role] || ROLE_ICONS[baseRole] || '\uD83D\uDC64'
                            const assignmentClasses = [
                              'calendar-chip',
                              'calendar-assignment',
                              a.status === 'swap_needed' ? 'needs-cover' : '',
                              selectedPerson && worker === selectedPerson ? 'filtered-person' : '',
                            ].filter(Boolean).join(' ')
                            return (
                              <div key={a.id} className={assignmentClasses} title={`${a.role}: ${worker || 'Unassigned'}`}>
                                <span className="calendar-role-icon">{icon}</span>
                                <span className="calendar-chip-text">{selectedPerson ? a.role : (worker || 'Unassigned')}</span>
                              </div>
                            )
                          })}
                          {assignments.length > 4 && (
                            <div className="calendar-chip calendar-more-chip">+{assignments.length - 4} more</div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Event Card
// ═══════════════════════════════════════════════════════════════

function EventCard({ event, user, isAdmin, isManager, doAction, onNotify, onAssign, onEventUpdate, onToggleLock, teamNames, recentlyChanged, selectedPerson }) {
  const [editingEvent, setEditingEvent] = useState(false)
  const [editType, setEditType] = useState(event.day_type === 'Sunday' ? 'Sunday' : event.day_type === 'Friday' ? 'Bible Study' : 'Other')
  const [editTitle, setEditTitle] = useState(event.custom_title || event.title || '')
  const [editDate, setEditDate] = useState(event.date)
  const [editTime, setEditTime] = useState(event.start_time || defaultStartTime(event.day_type))
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
      start_time: editTime,
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
  const TimeTag = canEdit ? 'button' : 'span'
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
          <div className="event-header-actions">
            <TimeTag
              className={`event-time-pill ${canEdit ? 'editable' : ''}`}
              {...editProps}
            >
              🕒 {formatDisplayTime(event.start_time || defaultStartTime(event.day_type))}
            </TimeTag>
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
              onChange={e => {
                setEditType(e.target.value)
                setEditTime(defaultStartTime(e.target.value === 'Bible Study' ? 'Friday' : e.target.value))
              }}
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
            <input
              type="time"
              value={editTime}
              onChange={e => setEditTime(e.target.value)}
              className="event-edit-input"
            />
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
              eventAssignments={event.assignments}
              user={user}
              isManager={isManager}
              doAction={doAction}
              onAssign={onAssign}
              onToggleLock={onToggleLock}
              teamNames={teamNames}
              isPast={event.is_past}
              isRecentlyChanged={!!(recentlyChanged && recentlyChanged.has && recentlyChanged.has(a.id))}
              selectedPerson={selectedPerson}
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

function AssignmentRow({ assignment: a, eventAssignments = [], user, isManager, doAction, onAssign, onToggleLock, teamNames, isPast, isRecentlyChanged, selectedPerson }) {
  const [showNames, setShowNames] = useState(false)
  const [menuPos, setMenuPos] = useState(null)
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const baseRole = a.role.replace(/\s+\d+$/, '')
  const icon = ROLE_ICONS[a.role] || ROLE_ICONS[baseRole] || '\uD83D\uDC64'
  const worker = a.cover || a.person
  const isMe = a.person === user || a.cover === user
  const alreadyAssignedThisEvent = Boolean(user && eventAssignments.some(other => other.id !== a.id && ((other.cover || other.person) === user)))
  const isUnassigned = a.person === 'Select Helper' || a.person === 'TBD'
  const isConfirmed = a.status === 'confirmed'
  const isFilteredPerson = selectedPerson && worker === selectedPerson
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
    <div className={`assignment-row ${a.status === 'swap_needed' ? 'swap-needed' : ''} ${isConfirmed ? 'confirmed' : ''} ${isMe ? 'is-me' : ''} ${a.locked ? 'pinned' : ''} ${isRecentlyChanged ? 'recently-changed' : ''}`}>
      <div className="assignment-left">
        <span className={`role-icon ${roleClass}`} aria-hidden="true">{icon}</span>
        {isManager ? (
          <div className="name-picker">
            <button
              ref={triggerRef}
              type="button"
              className={`person-name-btn ${isUnassigned ? 'unassigned' : ''} ${isFilteredPerson ? 'filtered-person' : ''}`}
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
          <span className={`person-name ${isUnassigned ? 'unassigned' : ''} ${isFilteredPerson ? 'filtered-person' : ''}`}>
            {personDisplay}
            {a.swapped_with && <span className="swap-tag"> (swapped with {a.swapped_with})</span>}
          </span>
        )}
      </div>
      <div className="assignment-right">
        {!isPast && (
          <>
            {isManager && !isUnassigned && onToggleLock && (
              <button
                type="button"
                className={`lock-btn ${a.locked ? 'on' : ''}`}
                onClick={() => onToggleLock(a.id, !a.locked)}
                title={a.locked ? 'Unpin from rebalance' : 'Pin from rebalance'}
                aria-pressed={!!a.locked}
                aria-label={a.locked ? 'Unpin assignment' : 'Pin assignment'}
              >
                {a.locked ? '\uD83D\uDD12' : '\uD83D\uDD13'}
              </button>
            )}

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
              <>
                <div className="status-badge confirmed" title="Confirmed">{'\u2713'}</div>
                <button className="action-btn undo" onClick={() => doAction('undo', a.id)}>
                  Undo
                </button>
              </>
            )}

            {user && a.status === 'swap_needed' && !isMe && !isUnassigned && !alreadyAssignedThisEvent && (
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

function AdminAddMenu({ onClose, onAddEvent, onYearOverview, onRoleSettings }) {
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
      aria-label="Manager tools"
    >
      <div className="modal-card action-choice-card">
        <div className="modal-header">
          <h2>Manager tools</h2>
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
          <div className="action-choice-section">
            <button className="action-choice compact" onClick={onRoleSettings}>
              <span className="action-choice-icon">{'\u2699\uFE0F'}</span>
              <span>
                <strong>Scheduling settings</strong>
                <small>Edit users, role eligibility, preferences, and monthly caps.</small>
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

const ROLE_SETTING_DEFS = [
  { key: 'Sunday:Computer', dayType: 'Sunday', role: 'Computer', label: 'Sunday \uD83D\uDDA5\uFE0F' },
  { key: 'Sunday:Camera 1', dayType: 'Sunday', role: 'Camera 1', label: 'Sunday \uD83C\uDFA51\uFE0F\u20E3' },
  { key: 'Sunday:Camera 2', dayType: 'Sunday', role: 'Camera 2', label: 'Sunday \uD83C\uDFA52\uFE0F\u20E3' },
  { key: 'Friday:Computer', dayType: 'Friday', role: 'Computer', label: 'Friday \uD83D\uDDA5\uFE0F' },
  { key: 'Friday:Camera', dayType: 'Friday', role: 'Camera', label: 'Friday \uD83C\uDFA5' },
]

const ROLE_PREFERENCE_OPTIONS = [
  { value: 'less', label: 'Less' },
  { value: 'normal', label: 'Normal' },
  { value: 'more', label: 'More' },
]

const ROLE_CAP_DEFS = [
  { key: 'sunday_per_month', label: 'Sundays / mo' },
  { key: 'friday_per_month', label: 'Fridays / mo' },
  { key: 'total_per_month', label: 'Total / mo' },
]

const defaultCapsForMember = (member) => ({
  sunday_per_month: member.name === 'Florian' ? 1 : 2,
  friday_per_month: member.name === 'Florian' ? 2 : 2,
  total_per_month: member.name === 'Florian' ? 3 : 4,
})

const cloneMemberSettings = (member) => {
  const preferences = { ...(member.role_preferences || {}) }
  delete preferences._caps
  return {
    ...member,
    sunday_roles: [...(member.sunday_roles || [])],
    friday_roles: [...(member.friday_roles || [])],
    role_preferences: preferences,
    caps: {
      ...defaultCapsForMember(member),
      ...((member.role_preferences || {})._caps || {}),
    },
  }
}

const newMemberSettings = () => {
  const id = `new-${Date.now()}`
  const member = {
    id,
    name: '',
    sunday_roles: ['Computer', 'Camera 1', 'Camera 2'],
    friday_roles: ['Computer', 'Camera'],
    role_preferences: {},
    active: true,
  }
  return {
    ...member,
    caps: defaultCapsForMember(member),
  }
}

function RoleSettingsModal({ team, onClose, onSaved, showFlash }) {
  const overlayRef = useRef(null)
  const initialMembers = useMemo(() => team.filter(m => m.id).map(cloneMemberSettings), [team])
  const [members, setMembers] = useState(() => initialMembers)
  const [removedIds, setRemovedIds] = useState(() => new Set())
  const [expandedId, setExpandedId] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const visibleMembers = members.filter(member => !removedIds.has(member.id))

  const updateMember = (id, updater) => {
    setMembers(prev => prev.map(member => member.id === id ? updater(member) : member))
  }

  const setName = (member, name) => {
    updateMember(member.id, current => ({ ...current, name }))
  }

  const toggleRole = (member, dayType, role) => {
    const field = dayType === 'Sunday' ? 'sunday_roles' : 'friday_roles'
    updateMember(member.id, current => {
      const selected = current[field].includes(role)
      return {
        ...current,
        [field]: selected ? current[field].filter(r => r !== role) : [...current[field], role],
      }
    })
  }

  const setPreference = (member, key, value) => {
    updateMember(member.id, current => ({
      ...current,
      role_preferences: {
        ...current.role_preferences,
        [key]: value,
      },
    }))
  }

  const setCap = (member, key, delta) => {
    updateMember(member.id, current => ({
      ...current,
      caps: {
        ...current.caps,
        [key]: Math.max(0, Number(current.caps?.[key] || 0) + delta),
      },
    }))
  }

  const addUser = () => {
    const member = newMemberSettings()
    setMembers(prev => [...prev, member])
    setExpandedId(member.id)
  }

  const removeUser = (member) => {
    if (String(member.id).startsWith('new-')) {
      setMembers(prev => prev.filter(item => item.id !== member.id))
    } else {
      setRemovedIds(prev => new Set([...prev, member.id]))
    }
    if (expandedId === member.id) setExpandedId(null)
  }

  const save = async () => {
    const invalid = visibleMembers.find(member => !member.name.trim())
    if (invalid) {
      showFlash('Every visible user needs a name', 'error')
      setExpandedId(invalid.id)
      return
    }

    setSubmitting(true)
    try {
      const result = await api('/team/apply-role-settings', {
        method: 'POST',
        body: JSON.stringify({
          removed_ids: [...removedIds].filter(id => !String(id).startsWith('new-')),
          members: visibleMembers.map(member => ({
            id: member.id,
            name: member.name.trim(),
            sunday_roles: member.sunday_roles,
            friday_roles: member.friday_roles,
            role_preferences: member.role_preferences,
            caps: member.caps,
            active: member.active !== false,
            telegram_user_id: member.telegram_user_id,
            active_from: member.active_from,
          })),
        }),
      })
      onSaved(result)
      onClose()
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
      aria-label="Scheduling settings"
    >
      <div className="modal-card role-settings-card">
        <div className="modal-header">
          <div>
            <h2>{'\u2699\uFE0F'} Scheduling Settings</h2>
            <p className="modal-subtitle">Expand a user to edit roles, preferences, caps, or add/remove users. Save refills only future schedule slots that need to change.</p>
          </div>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>
        <div className="modal-body role-settings-body compact">
          <div className="role-settings-list">
            {visibleMembers.map(member => {
              const expanded = expandedId === member.id
              const roleCount = (member.sunday_roles?.length || 0) + (member.friday_roles?.length || 0)
              return (
                <div key={member.id} className={`role-settings-member compact ${expanded ? 'expanded' : ''}`}>
                  <button type="button" className="role-settings-member-summary" onClick={() => setExpandedId(expanded ? null : member.id)}>
                    <span className="team-avatar small">{(member.name || '?')[0]}</span>
                    <strong>{member.name || 'New user'}</strong>
                    <span className="role-settings-summary-meta">{roleCount} roles</span>
                    <span className="role-settings-chevron">{expanded ? '\u25B2' : '\u25BC'}</span>
                  </button>

                  {expanded && (
                    <div className="role-settings-panel">
                      <label className="modal-field compact-name-field">
                        <span className="modal-label">Name</span>
                        <input
                          type="text"
                          value={member.name}
                          onChange={e => setName(member, e.target.value)}
                          className="modal-input"
                          placeholder="Team member name"
                        />
                      </label>

                      <div className="role-settings-rows">
                        {ROLE_SETTING_DEFS.map(def => {
                          const field = def.dayType === 'Sunday' ? 'sunday_roles' : 'friday_roles'
                          const selected = member[field].includes(def.role)
                          const value = member.role_preferences?.[def.key] || 'normal'
                          return (
                            <div key={def.key} className={`role-setting-row ${selected ? 'selected' : ''}`}>
                              <label className="role-setting-name">
                                <input type="checkbox" checked={selected} onChange={() => toggleRole(member, def.dayType, def.role)} />
                                <span>{def.label}</span>
                              </label>
                              <div className={`preference-pill-group pill-value-${value} ${selected ? '' : 'disabled'}`}>
                                {ROLE_PREFERENCE_OPTIONS.map(opt => (
                                  <button
                                    key={opt.value}
                                    type="button"
                                    className={`preference-pill-option ${value === opt.value ? 'active' : ''}`}
                                    onClick={() => setPreference(member, def.key, opt.value)}
                                    disabled={!selected}
                                  >
                                    {opt.label}
                                  </button>
                                ))}
                              </div>
                            </div>
                          )
                        })}
                      </div>

                      <div className="role-settings-caps">
                        <span className="modal-label">Monthly caps</span>
                        {ROLE_CAP_DEFS.map(def => (
                          <div key={def.key} className="cap-control expanded">
                            <span>{def.label}</span>
                            <button type="button" onClick={() => setCap(member, def.key, -1)} disabled={(member.caps?.[def.key] || 0) <= 0}>-</button>
                            <strong>{member.caps?.[def.key] ?? 0}</strong>
                            <button type="button" onClick={() => setCap(member, def.key, 1)}>+</button>
                          </div>
                        ))}
                      </div>

                      <button type="button" className="btn btn-danger btn-sm remove-user-inline" onClick={() => removeUser(member)}>
                        Remove user from future schedule
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          <button type="button" className="action-choice compact add-user-row" onClick={addUser}>
            <span className="action-choice-icon">+</span>
            <span>
              <strong>Add new user</strong>
              <small>Add roles, preferences, and caps here before saving.</small>
            </span>
          </button>
        </div>
        <div className="modal-footer">
          <span className="footer-hint">
            {removedIds.size ? `${removedIds.size} user${removedIds.size === 1 ? '' : 's'} marked for removal. ` : ''}
            Save updates future assignments while preserving confirmed/locked valid shifts.
          </span>
          <div className="footer-actions">
            <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
            <button className="btn btn-primary" onClick={save} disabled={submitting}>{submitting ? 'Saving…' : 'Save settings'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

const OVERVIEW_ROWS = [
  { key: 'S\uD83D\uDDA5', labelTop: 'Sun', labelBottom: '\uD83D\uDDA5\uFE0F' },
  { key: 'S\uD83C\uDFA51', labelTop: 'Sun', labelBottom: '\uD83C\uDFA51\uFE0F\u20E3' },
  { key: 'S\uD83C\uDFA52', labelTop: 'Sun', labelBottom: '\uD83C\uDFA52\uFE0F\u20E3' },
  { key: 'S\u03A3', labelTop: 'Sun', labelBottom: 'Total' },
  { key: 'F\uD83D\uDDA5', labelTop: 'Bible', labelBottom: '\uD83D\uDDA5\uFE0F' },
  { key: 'F\uD83C\uDFA5', labelTop: 'Bible', labelBottom: '\uD83C\uDFA5' },
  { key: 'F\u03A3', labelTop: 'Bible', labelBottom: 'Total' },
  { key: '\u03A3', labelTop: 'Total', labelBottom: '' },
]

const emptyOverviewCounts = (names) => names.reduce((acc, name) => {
  acc[name] = { 'S\uD83D\uDDA5': 0, 'S\uD83C\uDFA51': 0, 'S\uD83C\uDFA52': 0, 'S\u03A3': 0, 'F\uD83D\uDDA5': 0, 'F\uD83C\uDFA5': 0, 'F\u03A3': 0, '\u03A3': 0 }
  return acc
}, {})

const buildOverviewCounts = (events, names) => {
  const counts = emptyOverviewCounts(names)
  events.forEach(event => {
    ;(event.assignments || []).forEach(assignment => {
      const worker = assignment.cover || assignment.person
      if (!worker || worker === 'TBD' || worker === 'Select Helper') return
      if (!counts[worker]) counts[worker] = emptyOverviewCounts([worker])[worker]
      let key = null
      if (event.day_type === 'Sunday' && assignment.role === 'Computer') key = 'S\uD83D\uDDA5'
      if (event.day_type === 'Sunday' && assignment.role === 'Camera 1') key = 'S\uD83C\uDFA51'
      if (event.day_type === 'Sunday' && assignment.role === 'Camera 2') key = 'S\uD83C\uDFA52'
      if (event.day_type === 'Friday' && assignment.role === 'Computer') key = 'F\uD83D\uDDA5'
      if (event.day_type === 'Friday' && assignment.role === 'Camera') key = 'F\uD83C\uDFA5'
      if (!key) return
      counts[worker][key] += 1
      if (key.startsWith('S')) counts[worker]['S\u03A3'] += 1
      if (key.startsWith('F')) counts[worker]['F\u03A3'] += 1
      counts[worker]['\u03A3'] += 1
    })
  })
  return counts
}

function OverviewMatrix({ title, events, names }) {
  const counts = useMemo(() => buildOverviewCounts(events, names), [events, names])
  return (
    <div className="overview-section">
      <div className="snapshot-panel-title">{title}</div>
      <div className="year-overview-table-wrap">
        <table className="year-overview-table">
          <thead>
            <tr>
              <th>Name</th>
              {OVERVIEW_ROWS.map(row => (
                <th key={row.key}>
                  <span className={`overview-header-label ${row.key === '\u03A3' ? 'grand-total' : ''}`}>
                    <span>{row.labelTop}</span>
                    {row.labelBottom && <span>{row.labelBottom}</span>}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {names.map(name => (
              <tr key={name}>
                <td>{name}</td>
                {OVERVIEW_ROWS.map(row => <td key={row.key}>{counts[name]?.[row.key] || 0}</td>)}
              </tr>
            ))}
            <tr className="overview-total-row">
              <td>Total</td>
              {OVERVIEW_ROWS.map(row => (
                <td key={row.key}>{names.reduce((sum, name) => sum + (counts[name]?.[row.key] || 0), 0)}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

function YearOverviewModal({ schedule, team, onClose }) {
  const overlayRef = useRef(null)
  const years = [...new Set(schedule.map(event => event.date.slice(0, 4)))].sort()
  const currentYear = String(new Date().getFullYear())
  const [year, setYear] = useState(years.includes(currentYear) ? currentYear : years[years.length - 1])
  const activeNames = useMemo(() => (
    team
      .filter(member => member.active !== false)
      .map(member => member.name)
  ), [team])
  const yearEvents = useMemo(() => schedule.filter(event => event.date.startsWith(year) && ['Sunday', 'Friday'].includes(event.day_type)), [schedule, year])
  const names = useMemo(() => {
    const counts = buildOverviewCounts(yearEvents, activeNames)
    return [...activeNames].sort((a, b) => {
      if (a === 'Florian') return -1
      if (b === 'Florian') return 1
      const totalDiff = (counts[b]?.['\u03A3'] || 0) - (counts[a]?.['\u03A3'] || 0)
      return totalDiff || a.localeCompare(b)
    })
  }, [activeNames, yearEvents])
  const months = useMemo(() => [...new Set(yearEvents.map(event => event.date.slice(0, 7)))].sort(), [yearEvents])

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
      aria-label="Year overview"
    >
      <div className="modal-card year-overview-card">
        <div className="modal-header">
          <div>
            <h2>Year Overview</h2>
            <p className="modal-subtitle">Full-year totals first, then month-by-month breakdown.</p>
          </div>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>
        <div className="modal-body year-overview-body">
          <div className="role-focus-bar" role="tablist" aria-label="Year">
            {years.map(y => (
              <button key={y} type="button" className={`role-focus-tab ${year === y ? 'active' : ''}`} onClick={() => setYear(y)}>{y}</button>
            ))}
          </div>
          <OverviewMatrix title={`${year} totals`} events={yearEvents} names={names} />
          {months.map(month => (
            <OverviewMatrix
              key={month}
              title={new Date(`${month}-15`).toLocaleString('en', { month: 'long', year: 'numeric' })}
              events={yearEvents.filter(event => event.date.startsWith(month))}
              names={names}
            />
          ))}
        </div>
        <div className="modal-footer">
          <button className="btn btn-primary" onClick={onClose}>Done</button>
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

const SCOPE_OPTIONS = [
  { value: '1m', label: '1 month' },
  { value: '3m', label: '3 months' },
  { value: 'all', label: 'All future' },
]

const getMemberCaps = (member) => {
  const prefs = member?.role_preferences || {}
  const saved = prefs?._caps && typeof prefs._caps === 'object' ? prefs._caps : {}
  return {
    ...defaultCapsForMember(member || { name: '' }),
    ...saved,
    _custom: Boolean(prefs?._caps),
  }
}

const isProtectedAssignment = (assignment) => {
  const worker = assignment.cover || assignment.person
  return Boolean(worker && worker !== 'TBD' && worker !== 'Select Helper' && (assignment.locked || assignment.status === 'confirmed'))
}

function addMonths(date, months) {
  const d = new Date(date)
  d.setMonth(d.getMonth() + months)
  return d
}

function SchedulingControlsModal({ schedule, team, onClose, onApplied, onRestored, showFlash }) {
  const todayKey = toDateKey(new Date())
  const activeTeam = team.filter(member => member.name && member.active !== false)
  const overlayRef = useRef(null)
  const [scope, setScope] = useState('3m')
  const endDateKey = useMemo(() => {
    if (scope === 'all') return null
    const months = scope === '1m' ? 1 : 3
    const end = addMonths(new Date(), months)
    return toDateKey(end)
  }, [scope])

  const futureEvents = useMemo(() => (
    schedule.filter(event =>
      event.date >= todayKey
      && (!endDateKey || event.date <= endDateKey)
      && ['Sunday', 'Friday'].includes(event.day_type)
    )
  ), [schedule, todayKey, endDateKey])
  const futureMonths = useMemo(() => new Set(futureEvents.map(event => event.date.slice(0, 7))), [futureEvents])

  const initialTargets = useMemo(() => buildSchedulingTargets(futureEvents, activeTeam), [futureEvents, activeTeam])
  const [targets, setTargets] = useState(initialTargets)
  const [submitting, setSubmitting] = useState(false)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => { setTargets(initialTargets) }, [initialTargets])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { confirming ? setConfirming(false) : onClose() } }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, confirming])

  const [snapshots, setSnapshots] = useState([])
  const [presets, setPresets] = useState([])
  const [focusedRole, setFocusedRole] = useState('all')
  const [refreshReminders, setRefreshReminders] = useState(false)
  const [previewChanges, setPreviewChanges] = useState(null)
  const [previewing, setPreviewing] = useState(false)

  const loadSnapshots = useCallback(() => {
    api('/scheduling-controls/snapshots').then(setSnapshots).catch(() => setSnapshots([]))
  }, [])
  const loadPresets = useCallback(() => {
    api('/scheduling-controls/presets').then(setPresets).catch(() => setPresets([]))
  }, [])
  useEffect(() => { loadSnapshots(); loadPresets() }, [loadSnapshots, loadPresets])

  const restoreSnapshot = async (snapshotId) => {
    try {
      const res = await api('/scheduling-controls/undo', {
        method: 'POST',
        body: JSON.stringify({ snapshot_id: snapshotId }),
      })
      loadSnapshots()
      if (onRestored) {
        onRestored(res)
      } else {
        showFlash(`Restored ${res.restored} assignments`)
        onClose()
      }
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const lockedTotals = CONTROL_ROLES.reduce((acc, role) => {
    acc[role.key] = futureEvents.reduce((total, event) => {
      if (event.day_type !== role.dayType) return total
      return total + (event.assignments || []).filter(a => role.roles.includes(a.role) && isProtectedAssignment(a)).length
    }, 0)
    return acc
  }, {})
  const totalLocked = CONTROL_ROLES.reduce((sum, role) => sum + (lockedTotals[role.key] || 0), 0)

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
  const personDiff = useMemo(() => {
    const aggregated = {}
    CONTROL_ROLES.forEach(role => {
      const baseline = initialTargets[role.key] || {}
      const current = targets[role.key] || {}
      const names = new Set([...Object.keys(baseline), ...Object.keys(current)])
      names.forEach(name => {
        const change = (current[name] || 0) - (baseline[name] || 0)
        if (change !== 0) {
          aggregated[name] = (aggregated[name] || 0) + change
        }
      })
    })
    return Object.entries(aggregated)
      .filter(([, value]) => value !== 0)
      .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
  }, [targets, initialTargets])
  const totalChanges = personDiff.reduce((sum, [, value]) => sum + Math.abs(value), 0) / 2

  const monthsSpan = Math.max(futureMonths.size, 1)
  const capWarnings = useMemo(() => {
    const warnings = []
    activeTeam.forEach(member => {
      const caps = getMemberCaps(member)
      if (!caps._custom) return

      const sundayTotal = CONTROL_ROLES
        .filter(role => role.dayType === 'Sunday')
        .reduce((sum, role) => sum + (targets[role.key]?.[member.name] || 0), 0)
      const fridayTotal = CONTROL_ROLES
        .filter(role => role.dayType === 'Friday')
        .reduce((sum, role) => sum + (targets[role.key]?.[member.name] || 0), 0)
      const totalAcrossRoles = sundayTotal + fridayTotal

      const allowedSunday = (Number(caps.sunday_per_month) || 0) * monthsSpan
      const allowedFriday = (Number(caps.friday_per_month) || 0) * monthsSpan
      const allowedTotal = (Number(caps.total_per_month) || 0) * monthsSpan

      if (sundayTotal > allowedSunday) {
        warnings.push(`${member.name} Sunday total: ${sundayTotal} exceeds ${allowedSunday} (cap ${caps.sunday_per_month}/month × ${monthsSpan})`)
      }
      if (fridayTotal > allowedFriday) {
        warnings.push(`${member.name} Friday total: ${fridayTotal} exceeds ${allowedFriday} (cap ${caps.friday_per_month}/month × ${monthsSpan})`)
      }
      if (totalAcrossRoles > allowedTotal) {
        warnings.push(`${member.name} total: ${totalAcrossRoles} exceeds ${allowedTotal} (cap ${caps.total_per_month}/month × ${monthsSpan})`)
      }
    })
    return warnings
  }, [targets, monthsSpan, activeTeam])

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
    setTargets(initialTargets)
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
    setPreviewing(true)
    setPreviewChanges(null)
    try {
      const preview = await api('/scheduling-controls/preview', {
        method: 'POST',
        body: JSON.stringify({
          targets,
          end_date: endDateKey || undefined,
        }),
      })
      setPreviewChanges(preview.changes || [])
      setConfirming(true)
    } catch (e) {
      showFlash(e.message, 'error')
    } finally {
      setPreviewing(false)
    }
  }

  const performApply = async () => {
    setSubmitting(true)
    try {
      const result = await api('/scheduling-controls/apply', {
        method: 'POST',
        body: JSON.stringify({
          targets,
          end_date: endDateKey || undefined,
          label: scope === 'all' ? 'all-future' : `next-${scope}`,
        }),
      })
      if (refreshReminders) {
        try { await api('/scheduling-controls/refresh-reminders', { method: 'POST' }) }
        catch (e) { showFlash(`Apply succeeded but reminders failed: ${e.message}`, 'warning') }
      }
      const changedIds = (previewChanges || []).map(c => c.assignment_id).filter(Boolean)
      onApplied(result, changedIds)
    } catch (e) {
      showFlash(e.message, 'error')
    } finally {
      setSubmitting(false)
      setConfirming(false)
    }
  }

  const savePreset = async () => {
    const name = window.prompt('Preset name?')
    if (!name) return
    try {
      await api('/scheduling-controls/presets', {
        method: 'POST',
        body: JSON.stringify({ name, targets }),
      })
      showFlash(`Saved preset “${name}”`)
      loadPresets()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const loadPreset = (preset) => {
    setTargets(prev => {
      const next = {}
      CONTROL_ROLES.forEach(role => {
        const baseline = prev[role.key] || {}
        const incoming = (preset.targets && preset.targets[role.key]) || {}
        const merged = {}
        Object.keys(baseline).forEach(name => { merged[name] = incoming[name] || 0 })
        Object.keys(incoming).forEach(name => { merged[name] = incoming[name] })
        next[role.key] = merged
      })
      return next
    })
    showFlash(`Loaded preset “${preset.name}”`)
  }

  const deletePreset = async (presetId) => {
    if (!window.confirm('Delete this preset?')) return
    try {
      await api(`/scheduling-controls/presets/${presetId}`, { method: 'DELETE' })
      loadPresets()
    } catch (e) {
      showFlash(e.message, 'error')
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
              {totalSlots} future slots across {futureMonths.size} month{futureMonths.size === 1 ? '' : 's'}
              {totalLocked > 0 ? ` · ${totalLocked} locked/confirmed` : ''}. Past events stay untouched.
            </p>
          </div>
          <button className="icon-btn-sm" onClick={onClose} aria-label="Close">{'\u2715'}</button>
        </div>

        <div className="modal-body">
          <div className="control-toolbar">
            <div className="segmented scope-segmented" role="tablist" aria-label="Scope">
              {SCOPE_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  role="tab"
                  aria-selected={scope === opt.value}
                  className={`segment ${scope === opt.value ? 'active' : ''}`}
                  onClick={() => setScope(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button className="btn btn-ghost btn-sm" onClick={resetToCurrent} type="button">Reset</button>
            <button className="btn btn-outline btn-sm" onClick={distributeEvenly} type="button">Even split</button>
            <span className={`balance-status ${balanced ? 'balanced' : 'unbalanced'}`}>
              {balanced ? 'Balanced' : `${CONTROL_ROLES.filter(r => delta[r.key] !== 0).length} role${CONTROL_ROLES.filter(r => delta[r.key] !== 0).length === 1 ? '' : 's'} need attention`}
            </span>
          </div>

          {capWarnings.length > 0 && (
            <div className="warning-banner">
              <div className="warning-banner-title">Cap warnings</div>
              <ul>
                {capWarnings.map(w => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
              <small>Apply still works but the scheduler will likely fall back to relaxed rules to satisfy these targets.</small>
            </div>
          )}

          <div className="role-focus-bar" role="tablist" aria-label="Focused role">
            <button
              type="button"
              className={`role-focus-tab ${focusedRole === 'all' ? 'active' : ''}`}
              onClick={() => setFocusedRole('all')}
            >
              All roles
            </button>
            {CONTROL_ROLES.map(role => (
              <button
                key={role.key}
                type="button"
                className={`role-focus-tab ${focusedRole === role.key ? 'active' : ''}`}
                onClick={() => setFocusedRole(role.key)}
              >
                {role.label}
              </button>
            ))}
          </div>

          <div className="schedule-control-table-wrap">
            <table className="schedule-control-table">
              <thead>
                <tr>
                  <th>Person</th>
                  {CONTROL_ROLES.filter(role => focusedRole === 'all' || role.key === focusedRole).map(role => (
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
                    <tr key={member.id || member.name}>
                      <td>
                        <div className="person-cell">
                          <span className="team-avatar small">{member.name[0]}</span>
                          <span>{member.name}</span>
                        </div>
                      </td>
                      {CONTROL_ROLES.filter(role => focusedRole === 'all' || role.key === focusedRole).map(role => {
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
                  {CONTROL_ROLES.filter(role => focusedRole === 'all' || role.key === focusedRole).map(role => (
                    <td key={role.key} className={delta[role.key] === 0 ? 'balanced-total' : 'unbalanced-total'}>
                      {roleTotal(role.key)} / {slotTotals[role.key] || 0}
                    </td>
                  ))}
                  <td>{CONTROL_ROLES.reduce((sum, role) => sum + roleTotal(role.key), 0)}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="snapshot-panel">
            <div className="panel-row">
              <div className="snapshot-panel-title">Presets</div>
              <button className="btn btn-outline btn-sm" type="button" onClick={savePreset}>
                Save current as preset
              </button>
            </div>
            {presets.length === 0 ? (
              <div className="modal-help" style={{ marginTop: 4 }}>No saved presets yet.</div>
            ) : (
              <ul className="snapshot-list">
                {presets.map(preset => {
                  const peopleTotals = {}
                  CONTROL_ROLES.forEach(role => {
                    const counts = preset.targets?.[role.key] || {}
                    Object.entries(counts).forEach(([n, v]) => {
                      const num = Number(v) || 0
                      if (num > 0) peopleTotals[n] = (peopleTotals[n] || 0) + num
                    })
                  })
                  const top = Object.entries(peopleTotals).sort((a, b) => b[1] - a[1]).slice(0, 4)
                  return (
                    <li key={preset.id} className="snapshot-item preset-item">
                      <div className="snapshot-meta">
                        <strong>{preset.name}</strong>
                        <span>{preset.created_by ? `by ${preset.created_by} · ` : ''}{timeAgo(preset.created_at)}</span>
                        {top.length > 0 && (
                          <div className="preset-thumb">
                            {top.map(([name, count]) => (
                              <span key={name} className="preset-thumb-chip">{name} {count}</span>
                            ))}
                            {Object.keys(peopleTotals).length > top.length && (
                              <span className="preset-thumb-chip muted">+{Object.keys(peopleTotals).length - top.length}</span>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="footer-actions">
                        <button className="btn btn-ghost btn-sm" type="button" onClick={() => loadPreset(preset)}>Load</button>
                        <button className="btn btn-ghost btn-sm" type="button" onClick={() => deletePreset(preset.id)}>Delete</button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          <label className="reminder-toggle">
            <input
              type="checkbox"
              checked={refreshReminders}
              onChange={e => setRefreshReminders(e.target.checked)}
            />
            <span>Re-send today's Telegram reminders after applying</span>
          </label>

          {snapshots.length > 0 && (
            <div className="snapshot-panel">
              <div className="snapshot-panel-title">Recent applies</div>
              <ul className="snapshot-list">
                {snapshots.map(snap => (
                  <li key={snap.id} className="snapshot-item">
                    <div className="snapshot-meta">
                      <strong>{snap.label || 'apply'}</strong>
                      <span>{snap.size} change{snap.size === 1 ? '' : 's'} · {timeAgo(snap.created_at)}{snap.created_by ? ` · ${snap.created_by}` : ''}</span>
                    </div>
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      onClick={() => restoreSnapshot(snap.id)}
                    >
                      Restore
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <span className="footer-hint">{totalChanges > 0 ? `${totalChanges} change${totalChanges === 1 ? '' : 's'}` : 'No changes yet'}</span>
          <div className="footer-actions">
            <button className="btn btn-ghost" onClick={onClose} disabled={submitting || previewing}>Cancel</button>
            <button className="btn btn-primary" onClick={handleApply} disabled={submitting || previewing || !balanced || totalChanges === 0}>
              {previewing ? 'Previewing…' : submitting ? 'Applying…' : 'Apply to future schedule'}
            </button>
          </div>
        </div>
      </div>

      {confirming && (
        <ConfirmApplyModal
          totalSlots={totalSlots}
          totalChanges={totalChanges}
          months={futureMonths.size}
          totalLocked={totalLocked}
          personDiff={personDiff}
          warnings={capWarnings}
          scopeLabel={SCOPE_OPTIONS.find(opt => opt.value === scope)?.label || ''}
          previewChanges={previewChanges}
          submitting={submitting}
          refreshReminders={refreshReminders}
          onCancel={() => setConfirming(false)}
          onConfirm={performApply}
        />
      )}
    </div>
  )
}

function ConfirmApplyModal({ totalSlots, totalChanges, months, totalLocked, personDiff, warnings = [], scopeLabel, previewChanges, submitting, refreshReminders, onCancel, onConfirm }) {
  const previewCount = previewChanges ? previewChanges.length : null
  return (
    <div className="modal-overlay confirm-overlay" role="dialog" aria-modal="true" aria-label="Confirm apply">
      <div className="modal-card confirm-card">
        <div className="modal-header">
          <h2>Apply targets?</h2>
        </div>
        <div className="modal-body">
          <p className="modal-help">
            Rewrite <strong>{totalSlots}</strong> future slots across <strong>{months}</strong> month{months === 1 ? '' : 's'}
            {scopeLabel ? ` (scope: ${scopeLabel})` : ''}.
          </p>
          <ul className="modal-list">
            <li>{totalChanges} change{totalChanges === 1 ? '' : 's'} compared to current distribution.</li>
            {previewCount !== null && <li>Preview: {previewCount} actual event-level change{previewCount === 1 ? '' : 's'}.</li>}
            {totalLocked > 0 && <li>{totalLocked} locked/confirmed assignment{totalLocked === 1 ? '' : 's'} stay untouched.</li>}
            <li>Past events untouched. Custom events (baptisms, etc.) untouched.</li>
            <li>Florian caps and gap rules still apply where possible.</li>
            <li>You can Undo from the toast for 30s after applying.</li>
            {refreshReminders && <li>Today's Telegram reminders will be re-sent after apply.</li>}
          </ul>

          {warnings.length > 0 && (
            <div className="warning-banner">
              <div className="warning-banner-title">Heads up</div>
              <ul>
                {warnings.map(w => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}

          {personDiff.length > 0 && (
            <div className="diff-block">
              <div className="diff-title">Per person</div>
              <div className="diff-list">
                {personDiff.map(([name, value]) => (
                  <span key={name} className={`diff-chip ${value > 0 ? 'plus' : 'minus'}`}>
                    {name} {value > 0 ? `+${value}` : value}
                  </span>
                ))}
              </div>
            </div>
          )}

          {previewChanges && previewChanges.length > 0 && (
            <div className="diff-block">
              <div className="diff-title">Per event</div>
              <ul className="event-diff-list">
                {previewChanges.slice(0, 12).map((change, idx) => (
                  <li key={`${change.assignment_id}-${idx}`} className="event-diff-row">
                    <span className="event-diff-date">{change.date}</span>
                    <span className="event-diff-role">{change.day_type === 'Friday' ? 'Bible' : 'Sunday'} {change.role}</span>
                    <span className="event-diff-arrow">{change.from || '—'} → {change.to || '—'}</span>
                  </li>
                ))}
                {previewChanges.length > 12 && (
                  <li className="event-diff-more">…and {previewChanges.length - 12} more</li>
                )}
              </ul>
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onCancel} disabled={submitting}>Back</button>
          <button className={`btn ${warnings.length ? 'btn-warning' : 'btn-primary'}`} onClick={onConfirm} disabled={submitting}>
            {submitting ? 'Applying…' : warnings.length ? 'Apply anyway' : 'Yes, apply'}
          </button>
        </div>
      </div>
    </div>
  )
}

function buildSchedulingTargets(events, team) {
  const names = team.map(member => member.name)
  const initial = CONTROL_ROLES.reduce((acc, role) => {
    acc[role.key] = names.reduce((people, name) => {
      people[name] = 0
      return people
    }, {})
    return acc
  }, {})

  events.forEach(event => {
    if (!['Sunday', 'Friday'].includes(event.day_type)) return
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
  const [startTime, setStartTime] = useState(normalizeSuggestedTime(prefill?.time) || defaultStartTime(prefill ? initialType : 'Sunday'))
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
    setStartTime(defaultStartTime(newType))
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
          start_time: startTime,
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

          <label className="modal-field">
            <span className="modal-label">Time</span>
            <input
              type="time"
              value={startTime}
              onChange={e => setStartTime(e.target.value)}
              className="modal-input"
            />
          </label>

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
  const lockedName = Boolean(defaultName)
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
    const suggesterName = (defaultName || name).trim()
    if (!suggesterName) { showFlash('Please enter your name', 'error'); return }
    if (eventType === 'Other' && !customTitle.trim()) { showFlash('Please enter a title', 'error'); return }
    if (!date) { showFlash('Please pick a date', 'error'); return }
    setSubmitting(true)
    try {
      await api('/suggestions', {
        method: 'POST',
        body: JSON.stringify({
          suggester_name: suggesterName,
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
            Suggest a livestream event if you know the date and time.
          </p>

          {!lockedName && (
            <label className="modal-field">
              <span className="modal-label">Your name</span>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                className="modal-input"
                placeholder="Enter your name"
                autoFocus
              />
            </label>
          )}

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
