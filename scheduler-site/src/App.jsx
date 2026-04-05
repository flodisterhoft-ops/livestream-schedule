import { useState, useEffect, useCallback } from 'react'

const API = '/api/v2'

const ROLE_ICONS = {
  Computer: '\uD83D\uDDA5\uFE0F',
  'Camera 1': '\uD83D\uDCF9',
  'Camera 2': '\uD83C\uDFA5',
  Leader: '\uD83D\uDCD6',
  Helper: '\uD83E\uDD1D',
}

const STATUS_COLORS = {
  confirmed: '#10b981',
  pending: '#f59e0b',
  swap_needed: '#ef4444',
}

const STATUS_LABELS = {
  confirmed: 'Confirmed',
  pending: 'Pending',
  swap_needed: 'Needs Coverage',
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
  const [isManager, setIsManager] = useState(false)
  const [tab, setTab] = useState('schedule')
  const [schedule, setSchedule] = useState([])
  const [team, setTeam] = useState([])
  const [stats, setStats] = useState({})
  const [loading, setLoading] = useState(true)
  const [flash, setFlash] = useState(null)
  const [selectedMonth, setSelectedMonth] = useState(null)

  // ── Init ──────────────────────────────────────────────────
  useEffect(() => {
    api('/auth/me').then(d => {
      if (d.name) {
        setUser(d.name)
        setIsManager(d.is_manager)
      }
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const loadSchedule = useCallback(() => {
    api('/schedule').then(setSchedule).catch(console.error)
  }, [])

  const loadTeam = useCallback(() => {
    api('/team').then(setTeam).catch(console.error)
  }, [])

  const loadStats = useCallback(() => {
    api('/leaderboard').then(d => setStats(d.stats || {})).catch(console.error)
  }, [])

  useEffect(() => {
    if (user) {
      loadSchedule()
      loadTeam()
      loadStats()
    }
  }, [user, loadSchedule, loadTeam, loadStats])

  const showFlash = (msg, type = 'success') => {
    setFlash({ msg, type })
    setTimeout(() => setFlash(null), 3000)
  }

  // ── Login ─────────────────────────────────────────────────
  const handleLogin = async (name, password) => {
    try {
      const d = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ name, password }),
      })
      setUser(d.name)
      setIsManager(false)
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleLogout = async () => {
    await api('/auth/logout', { method: 'POST' }).catch(() => {})
    setUser(null)
    setIsManager(false)
  }

  const toggleManager = async () => {
    try {
      const d = await api('/auth/manager', {
        method: 'POST',
        body: JSON.stringify({ pin: '2026' }),
      })
      setIsManager(d.is_manager)
      showFlash(d.is_manager ? 'Manager mode enabled' : 'Manager mode disabled')
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
  if (loading) return <div className="app-loading">Loading...</div>

  if (!user) return <LoginScreen team={team} onLogin={handleLogin} loadTeam={loadTeam} />

  // Calculate months for navigation
  const months = [...new Set(schedule.map(e => e.date.slice(0, 7)))].sort()
  const currentMonth = new Date().toISOString().slice(0, 7)
  const activeMonth = selectedMonth || (months.includes(currentMonth) ? currentMonth : months[months.length - 1])

  const filtered = activeMonth
    ? schedule.filter(e => e.date.startsWith(activeMonth))
    : schedule

  return (
    <div className="app">
      {/* Flash message */}
      {flash && (
        <div className={`flash flash-${flash.type}`}>{flash.msg}</div>
      )}

      {/* Header */}
      <header className="app-header">
        <h1 className="app-title">Livestream Schedule</h1>
        <div className="header-actions">
          {user === 'Florian' && (
            <button
              className={`icon-btn ${isManager ? 'active' : ''}`}
              onClick={toggleManager}
              title={isManager ? 'Exit Manager' : 'Manager Mode'}
            >
              {isManager ? '\uD83D\uDEE1\uFE0F' : '\uD83D\uDD12'}
            </button>
          )}
          <button className="icon-btn" onClick={handleLogout} title="Logout">
            {'\uD83D\uDEAA'}
          </button>
        </div>
      </header>

      {/* Tab navigation */}
      <nav className="tab-nav">
        {['schedule', 'leaderboard', 'team'].map(t => (
          <button
            key={t}
            className={`tab-btn ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'schedule' && '\uD83D\uDCC5 Schedule'}
            {t === 'leaderboard' && '\uD83C\uDFC6 Leaderboard'}
            {t === 'team' && '\uD83D\uDC65 Team'}
          </button>
        ))}
      </nav>

      {/* Tab content */}
      {tab === 'schedule' && (
        <ScheduleTab
          schedule={filtered}
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
      )}
      {tab === 'leaderboard' && <LeaderboardTab stats={stats} />}
      {tab === 'team' && (
        <TeamTab
          team={team}
          isManager={isManager}
          loadTeam={loadTeam}
          showFlash={showFlash}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Login Screen
// ═══════════════════════════════════════════════════════════════

function LoginScreen({ team, onLogin, loadTeam }) {
  const [showPassword, setShowPassword] = useState(false)
  const [password, setPassword] = useState('')

  useEffect(() => { loadTeam() }, [loadTeam])

  const names = team.length > 0
    ? team.map(m => m.name).sort()
    : ['Andy', 'Florian', 'Marvin', 'Patric', 'Rene', 'Stefan', 'Viktor']

  const handleClick = (name) => {
    if (name === 'Florian') {
      setShowPassword(true)
    } else {
      onLogin(name)
    }
  }

  if (showPassword) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <h2>Florian</h2>
          <p className="login-subtitle">Enter password</p>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && onLogin('Florian', password)}
            placeholder="Password"
            autoFocus
            className="login-input"
          />
          <div className="login-actions">
            <button className="btn btn-primary" onClick={() => onLogin('Florian', password)}>
              Login
            </button>
            <button className="btn btn-ghost" onClick={() => { setShowPassword(false); setPassword('') }}>
              Back
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="login-screen">
      <div className="login-header">
        <h1>Livestream Schedule</h1>
        <p>Who are you?</p>
      </div>
      <div className="login-grid">
        {names.map(name => (
          <button
            key={name}
            className={`login-name-btn ${name === 'Florian' ? 'admin' : ''}`}
            onClick={() => handleClick(name)}
          >
            <span className="login-avatar">{name[0]}</span>
            <span className="login-name">{name}</span>
            {name === 'Florian' && <span className="login-badge">Admin</span>}
          </button>
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
//  Schedule Tab
// ═══════════════════════════════════════════════════════════════

function ScheduleTab({ schedule, months, activeMonth, onMonthChange, user, isManager, doAction, showFlash, loadSchedule, team }) {
  const [showGenerate, setShowGenerate] = useState(false)
  const [genMonth, setGenMonth] = useState('')

  const handleGenerate = async () => {
    if (!genMonth) return
    const [y, m] = genMonth.split('-').map(Number)
    try {
      const d = await api('/generate', {
        method: 'POST',
        body: JSON.stringify({ year: y, month: m }),
      })
      showFlash(`Generated ${d.created} events!`)
      loadSchedule()
      setShowGenerate(false)
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

  const handleWipe = async () => {
    if (!genMonth) return
    if (!confirm(`Wipe all events for ${genMonth}?`)) return
    const [y, m] = genMonth.split('-').map(Number)
    try {
      const d = await api('/wipe', {
        method: 'POST',
        body: JSON.stringify({ year: y, month: m }),
      })
      showFlash(`Wiped ${d.deleted} events`)
      loadSchedule()
    } catch (e) {
      showFlash(e.message, 'error')
    }
  }

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

  const handleTestTelegram = async (type) => {
    try {
      await api('/telegram/test', {
        method: 'POST',
        body: JSON.stringify({ type }),
      })
      showFlash('Test message sent to your personal chat!')
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

  const teamNames = team.length > 0
    ? team.map(m => m.name).sort()
    : ['Andy', 'Florian', 'Marvin', 'Patric', 'Rene', 'Stefan', 'Viktor', 'TBD']

  return (
    <div className="schedule-tab">
      {/* Manager controls */}
      {isManager && (
        <div className="manager-panel">
          <div className="manager-row">
            <input
              type="month"
              value={genMonth}
              onChange={e => setGenMonth(e.target.value)}
              className="input-month"
            />
            <button className="btn btn-primary btn-sm" onClick={handleGenerate}>
              Generate
            </button>
            <button className="btn btn-danger btn-sm" onClick={handleWipe}>
              Wipe
            </button>
          </div>
          <div className="manager-row">
            <button className="btn btn-sm btn-outline" onClick={() => handleTestTelegram('reminder')}>
              Test Reminder
            </button>
            <button className="btn btn-sm btn-outline" onClick={() => handleTestTelegram('monthly')}>
              Test Monthly
            </button>
          </div>
        </div>
      )}

      {/* Month navigation */}
      <div className="month-nav">
        {months.map(m => {
          const label = new Date(m + '-15').toLocaleString('en', { month: 'short' })
          const isPast = m < new Date().toISOString().slice(0, 7)
          return (
            <button
              key={m}
              className={`month-pill ${m === activeMonth ? 'active' : ''} ${isPast ? 'past' : ''}`}
              onClick={() => onMonthChange(m)}
            >
              {label}
            </button>
          )
        })}
      </div>

      {/* Events */}
      <div className="events-list">
        {schedule.length === 0 && (
          <div className="empty-state">
            <p>No events for this month.</p>
            {isManager && <p>Use the controls above to generate a schedule.</p>}
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

function EventCard({ event, user, isManager, doAction, onNotify, onAssign, teamNames }) {
  const d = new Date(event.date + 'T12:00:00')
  const dayNum = d.getDate()
  const dayName = d.toLocaleString('en', { weekday: 'short' })
  const monthName = d.toLocaleString('en', { month: 'short' })

  return (
    <div className={`event-card ${event.is_past ? 'past' : ''}`}>
      <div className="event-date-col">
        <span className="event-day-name">{dayName}</span>
        <span className="event-day-num">{dayNum}</span>
        <span className="event-month">{monthName}</span>
      </div>
      <div className="event-info">
        <div className="event-header">
          <span className="event-title">{event.title}</span>
          {isManager && !event.is_past && (
            <button className="icon-btn-sm" onClick={onNotify} title="Send Telegram">
              {'\uD83D\uDCE8'}
            </button>
          )}
        </div>
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
  const icon = ROLE_ICONS[a.role] || '\uD83D\uDC64'
  const worker = a.cover || a.person
  const isMe = a.person === user || a.cover === user
  const isUnassigned = a.person === 'Select Helper' || a.person === 'TBD'

  return (
    <div className={`assignment-row ${a.status === 'swap_needed' ? 'swap-needed' : ''} ${isMe ? 'is-me' : ''}`}>
      <div className="assignment-left">
        <span className="role-icon">{icon}</span>
        {isManager && !isPast ? (
          <select
            value={a.person}
            onChange={e => onAssign(a.id, e.target.value)}
            className="assign-select"
          >
            {isUnassigned && <option value="Select Helper">Unassigned</option>}
            {teamNames.map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        ) : (
          <span className={`person-name ${isUnassigned ? 'unassigned' : ''}`}>
            {isUnassigned ? 'Unassigned' : worker}
            {a.cover && <span className="cover-tag"> (covering)</span>}
            {a.swapped_with && <span className="swap-tag"> (swapped with {a.swapped_with})</span>}
          </span>
        )}
      </div>
      <div className="assignment-right">
        {!isPast && (
          <>
            {isUnassigned && !isManager && (
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

            {a.status === 'swap_needed' && !isMe && !isUnassigned && (
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
  const [newFridayRoles, setNewFridayRoles] = useState(['Leader'])

  const ALL_SUNDAY_ROLES = ['Computer', 'Camera 1', 'Camera 2']
  const ALL_FRIDAY_ROLES = ['Leader']

  const toggleRole = (role, list, setter) => {
    if (list.includes(role)) {
      setter(list.filter(r => r !== role))
    } else {
      setter([...list, role])
    }
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
        }),
      })
      showFlash(`${newName} added to team!`)
      setNewName('')
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
              <label key={role} className="role-toggle">
                <input
                  type="checkbox"
                  checked={newSundayRoles.includes(role)}
                  onChange={() => toggleRole(role, newSundayRoles, setNewSundayRoles)}
                />
                {ROLE_ICONS[role]} {role}
              </label>
            ))}
          </div>
          <div className="role-toggles">
            <label className="role-group-label">Friday Roles:</label>
            {ALL_FRIDAY_ROLES.map(role => (
              <label key={role} className="role-toggle">
                <input
                  type="checkbox"
                  checked={newFridayRoles.includes(role)}
                  onChange={() => toggleRole(role, newFridayRoles, setNewFridayRoles)}
                />
                {ROLE_ICONS[role]} {role}
              </label>
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
