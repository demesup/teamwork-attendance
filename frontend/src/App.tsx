import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowDownToLine, ArrowRight, ArrowUpRight, Bell, BookOpen, CalendarDays, Check, CheckCheck, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Clock3, Code2, Copy, Cpu, Eye, EyeOff, FlaskConical, GraduationCap, Hash, History, LayoutDashboard, LogOut, Menu, Plus, RefreshCw, Search, Settings, ShieldCheck, Sparkles, UserRound, Users, X, XCircle, Zap, LoaderCircle, PanelLeftClose, MapPin } from 'lucide-react'
import { api, ApiError, currentPosition, hasToken, setUnauthorizedHandler, type ClassStats, type Notification, type RecordRow, type Section, type SessionCredentials, type SessionSummary, type StudentStats, type User } from './api'

type Modal = 'logout' | 'end' | 'cancel' | 'help' | null
const COLORS = ['purple', 'blue', 'orange', 'green']
const ICONS = [Cpu, Code2, Zap, FlaskConical]
const DEMO_PASSWORD = 'demo-password-123'
const DEMO_ACCOUNTS = { student: 'alex.popescu@student.university.edu', professor: 'elena.ionescu@university.edu' }

const fullName = (u: { first_name: string; last_name: string }) => `${u.first_name} ${u.last_name}`
const initials = (u: { first_name: string; last_name: string }) => `${u.first_name[0] ?? ''}${u.last_name[0] ?? ''}`.toUpperCase()
const formatDate = (iso: string) => new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
const formatTime = (iso: string) => new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
const message = (err: unknown) => err instanceof Error ? err.message : 'Something went wrong.'
const semesterLabel = (d = new Date()) => `${d.getMonth() >= 7 ? 'Fall' : 'Spring'} Semester ${d.getFullYear()}`

function downloadCsv(filename: string, rows: string[][]) {
  const csv = rows.map(r => r.map(v => `"${v.replace(/"/g, '""')}"`).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }))
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url)
}

function Button({ children, variant = '', className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }) { return <button className={`btn ${variant} ${className}`} {...props}>{children}</button> }
function Badge({ children, color = 'green' }: { children: ReactNode; color?: string }) { return <span className={`badge ${color}`}><span className="status-dot" />{children}</span> }
const statusColor = (status: string) => status === 'present' ? 'green' : status === 'late' ? 'orange' : 'red'

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(hasToken())
  const [toast, setToast] = useState('')

  useEffect(() => { setUnauthorizedHandler(() => { setUser(null); setToast('Your session has expired. Please sign in again.') }) }, [])
  useEffect(() => {
    if (!hasToken()) return
    api.me().then(setUser).catch(() => api.logout()).finally(() => setBooting(false))
  }, [])
  useEffect(() => { if (toast) { const id = setTimeout(() => setToast(''), 4500); return () => clearTimeout(id) } }, [toast])

  if (booting) return <div className="login-page"><div className="login-form-side"><LoaderCircle className="spin" size={28} /></div></div>
  return <>
    {user
      ? <Workspace user={user} notify={setToast} onLogout={() => { api.logout(); setUser(null) }} />
      : <Login onLogin={u => { setUser(u); setToast(`Welcome back, ${u.first_name}!`) }} />}
    {toast && <div className="toast" role="status"><span><CheckCircle2 size={19} /></span>{toast}<button onClick={() => setToast('')} aria-label="Dismiss notification"><X size={16} /></button></div>}
  </>
}

function Workspace({ user, notify, onLogout }: { user: User; notify: (m: string) => void; onLogout: () => void }) {
  const isTeacher = user.role === 'professor'
  const [page, setPage] = useState('Dashboard')
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [modal, setModal] = useState<Modal>(null)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [courseDetail, setCourseDetail] = useState<Section | null>(null)
  const [historyCourse, setHistoryCourse] = useState('All courses')

  const [sections, setSections] = useState<Section[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [stats, setStats] = useState<StudentStats | null>(null)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loadError, setLoadError] = useState('')
  // Codes are only returned when a session is opened/rotated; keep them in memory, never in storage.
  const [credentials, setCredentials] = useState<Record<string, SessionCredentials>>({})
  const [activeId, setActiveId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextSections, nextSessions, nextNotifications, nextStats] = await Promise.all([
        api.sections(),
        api.sessions(isTeacher ? {} : { open: true }),
        api.notifications(),
        isTeacher ? Promise.resolve(null) : api.myStats(),
      ])
      setSections(nextSections); setSessions(nextSessions); setNotifications(nextNotifications); setStats(nextStats); setLoadError('')
    } catch (err) {
      setLoadError(message(err))
    }
  }, [isTeacher])
  useEffect(() => { refresh() }, [refresh])

  const openSessions = useMemo(() => sessions.filter(s => s.status === 'open'), [sessions])
  const activeSession = openSessions.find(s => s.id === activeId) ?? openSessions[0] ?? null
  const now = useNow(openSessions.length > 0)
  useEffect(() => { if (openSessions.some(s => Date.parse(s.expires_at) <= now)) refresh() }, [now, openSessions, refresh])

  // Live updates while a professor has a session open.
  useEffect(() => {
    if (!isTeacher || !activeSession) return
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [isTeacher, activeSession, refresh])

  function navigate(next: string) { setPage(next); setMobileNav(false); setCourseDetail(null); setNotificationsOpen(false) }

  async function endSession(kind: 'end' | 'cancel') {
    if (!activeSession) return
    try {
      if (kind === 'end') { await api.closeSession(activeSession.id); notify('Session closed. All attendance records have been saved.') }
      else { await api.cancelSession(activeSession.id); notify('Session cancelled. Enrolled students were notified.') }
      setCredentials(c => { const next = { ...c }; delete next[activeSession.id]; return next })
      await refresh()
    } catch (err) { notify(message(err)) }
  }

  const studentNav = [{ name: 'Dashboard', icon: LayoutDashboard }, { name: 'Submit Attendance', icon: CheckCheck }, { name: 'My Courses', icon: BookOpen }, { name: 'Attendance History', icon: History }]
  const teacherNav = [{ name: 'Dashboard', icon: LayoutDashboard }, { name: 'My Courses', icon: BookOpen }, { name: 'Create Lab Session', icon: Plus }, { name: 'Active Sessions', icon: FlaskConical }, { name: 'Attendance Records', icon: History }, { name: 'Students', icon: Users }]
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  const subtitle: Record<string, string> = {
    'Submit Attendance': 'One code. One click. Your attendance, taken care of.',
    'My Courses': isTeacher ? 'Your course sections and enrollments.' : 'Your learning journey, one lab at a time.',
    'Create Lab Session': 'Bring your class together. Set up your next lab session.',
    Profile: 'Your university identity, all in one place.',
    Settings: 'Make fafo work the way you do.',
  }

  return <div className={`app ${collapsed ? 'sidebar-collapsed' : ''}`}>
    {mobileNav && <div className="sidebar-overlay" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
      <a className="brand" href="#" onClick={e => { e.preventDefault(); navigate('Dashboard') }}><span className="brand-icon"><GraduationCap size={25} strokeWidth={1.8} /></span><span>fafo<span className="brand-dot">.</span></span></a>
      <div className="workspace"><span className="workspace-icon"><GraduationCap size={18} /></span><div><strong>University workspace</strong><span>{isTeacher ? 'Teacher portal' : 'Student portal'}</span></div><ChevronDown size={14} /></div>
      <span className="nav-label">WORKSPACE</span>
      <nav>{(isTeacher ? teacherNav : studentNav).map(item => <button key={item.name} className={`nav-item ${page === item.name ? 'active' : ''}`} onClick={() => navigate(item.name)} title={item.name}><item.icon size={18} /><span>{item.name}</span>{item.name === 'My Courses' && <span className="nav-count">{sections.length}</span>}{item.name === 'Active Sessions' && openSessions.length > 0 && <span className="nav-count">{openSessions.length}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><span className="nav-label">PERSONAL</span><button className={`nav-item ${page === 'Profile' ? 'active' : ''}`} onClick={() => navigate('Profile')}><UserRound size={18} /><span>Profile</span></button><button className={`nav-item ${page === 'Settings' ? 'active' : ''}`} onClick={() => navigate('Settings')}><Settings size={18} /><span>Settings</span></button><button className="nav-item" onClick={() => setModal('help')}><CircleHelp size={18} /><span>Help & support</span><ArrowUpRight size={14} /></button>
        <div className="semester-box"><span className="semester-icon"><Sparkles size={18} /></span><strong>A little presence.<br />A lot of possibility.</strong><p>Every lab is a step forward.<br />Keep showing up.</p><div>{semesterLabel().toUpperCase()}</div></div>
        <div className="sidebar-user"><button onClick={() => navigate('Profile')}><span className="avatar">{initials(user)}</span><span><strong>{fullName(user)}</strong><small>{isTeacher ? 'Faculty' : user.email}</small></span></button><button title="Log out" aria-label="Log out" onClick={() => setModal('logout')}><LogOut size={17} /></button></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button collapse-desktop" title="Toggle sidebar" onClick={() => setCollapsed(!collapsed)}><PanelLeftClose size={18} /></button><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={21} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{page}</strong></div>
        <div className="top-actions"><span className="semester-pill"><span className="mini-dot" />{semesterLabel()}</span>
          <div className="notification-wrap"><button className="notification-button icon-button" aria-label="Notifications" onClick={() => { setNotificationsOpen(!notificationsOpen); if (!notificationsOpen) refresh() }}><Bell size={19} />{notifications.length > 0 && <span />}</button>
            {notificationsOpen && <div className="notifications panel"><div className="section-head"><h3>Notifications</h3><button className="icon-button" onClick={() => setNotificationsOpen(false)} aria-label="Close notifications"><X size={15} /></button></div>
              {notifications.length === 0 ? <p><span className="notice-icon green"><CheckCircle2 size={17} /></span><span><strong>You're all caught up.</strong><small>New updates will appear here.</small></span></p>
                : notifications.slice(0, 8).map(n => <p key={n.id}><span className="notice-icon"><Bell size={17} /></span><span><strong>{n.title}</strong><small>{n.message}</small></span></p>)}
            </div>}
          </div><span className="header-divider" /><button className="avatar top-avatar" onClick={() => navigate('Profile')} aria-label="Open profile">{initials(user)}</button></div></header>
      <main>
        <div className="page-heading"><div><div className="page-eyebrow">{page === 'Dashboard' ? 'YOUR CAMPUS, CONNECTED' : isTeacher ? 'TEACHER WORKSPACE' : 'STUDENT WORKSPACE'}</div><h1>{page === 'Dashboard' ? `Welcome back, ${isTeacher ? `Prof. ${user.last_name}` : user.first_name}` : page}<span className="heading-dot">{page === 'Dashboard' ? '.' : ''}</span></h1><p>{page === 'Dashboard' ? isTeacher ? 'Great teaching starts with being present. Let’s get your labs ready.' : "A new day to show up, learn, and move forward. Here’s your overview." : subtitle[page] ?? 'Stay connected to every lab and every check-in.'}</p></div><div className="date-label"><CalendarDays size={16} />{today}</div></div>
        {loadError && <div className="form-error" role="alert"><XCircle size={15} />{loadError}<Button onClick={refresh}><RefreshCw size={14} />Retry</Button></div>}

        {isTeacher ? <>
          {page === 'Dashboard' && <TeacherDashboard sections={sections} sessions={sessions} activeSession={activeSession} credentials={credentials} setCredentials={setCredentials} now={now} navigate={navigate} notify={notify} onEnd={setModal} />}
          {page === 'My Courses' && <><CourseCards sections={sections} isTeacher onOpen={setCourseDetail} /><CreateSectionForm notify={notify} onCreated={refresh} /></>}
          {page === 'Create Lab Session' && <CreateSessionForm sections={sections} notify={notify} onCreated={async creds => { setCredentials(c => ({ ...c, [creds.id]: creds })); setActiveId(creds.id); await refresh(); navigate('Active Sessions') }} />}
          {page === 'Active Sessions' && (openSessions.length === 0
            ? <EmptyState icon={<FlaskConical size={28} />} title="No active sessions" text="Start a lab session to generate an attendance code."><Button variant="primary" onClick={() => navigate('Create Lab Session')}><Plus size={16} />Create lab session</Button></EmptyState>
            : <>{openSessions.length > 1 && <div className="filter-bar"><select aria-label="Active session" value={activeSession?.id} onChange={e => setActiveId(e.target.value)}>{openSessions.map(s => <option key={s.id} value={s.id}>{s.section_name} · {s.title || 'Lab session'}</option>)}</select></div>}
              {activeSession && <><SessionDisplay session={activeSession} credentials={credentials[activeSession.id]} setCredentials={setCredentials} now={now} notify={notify} onEnd={setModal} /><div className="section-spacer" /><LiveTable session={activeSession} live /></>}</>)}
          {page === 'Attendance Records' && <RecordsPage sessions={sessions} />}
          {page === 'Students' && <StudentsPage sections={sections} notify={notify} onChanged={refresh} />}
        </> : <>
          {page === 'Dashboard' && <StudentDashboard sections={sections} stats={stats} openSessions={openSessions} now={now} onConfirmed={refresh} notify={notify} navigate={navigate} onOpenCourse={setCourseDetail} />}
          {page === 'Submit Attendance' && <div className="focused-page"><AttendanceForm onConfirmed={refresh} notify={notify} autoFocus /><section className="panel how-it-works"><h2>Present in three simple steps</h2>{['Join your scheduled laboratory session.', 'Get the 6-character code from your teacher.', 'Enter it above. You’re all checked in.'].map((text, i) => <p key={text}><span>{i + 1}</span>{text}</p>)}</section><HistoryTable stats={stats} sections={sections} onViewAll={() => navigate('Attendance History')} /></div>}
          {page === 'My Courses' && <CourseCards sections={sections} isTeacher={false} onOpen={setCourseDetail} />}
          {page === 'Attendance History' && <HistoryTable stats={stats} sections={sections} full initialCourse={historyCourse} notify={notify} />}
        </>}
        {page === 'Profile' && <ProfilePanel user={user} />}
        {page === 'Settings' && <div className="settings-layout"><section className="panel settings-panel"><h2>Account</h2><p>Signed in as {user.email}.</p><div className="setting-row"><div><h3>Sign out of your account</h3><p>You can sign back in at any time.</p></div><Button onClick={() => setModal('logout')}><LogOut size={15} />Sign out</Button></div></section></div>}
        <footer className="main-footer"><span>© {new Date().getFullYear()} fafo. A little presence makes a difference.</span><span><span className="mini-dot" />{loadError ? 'API unreachable' : 'Connected'}</span></footer>
      </main>
    </div>
    {modal && <div className="modal-backdrop" onClick={() => setModal(null)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close dialog" onClick={() => setModal(null)}><X size={19} /></button><span className="modal-symbol">{modal === 'help' ? <CircleHelp size={25} /> : modal === 'logout' ? <LogOut size={25} /> : <FlaskConical size={25} />}</span>
      <h2 id="modal-title">{{ logout: 'See you next lab?', end: 'End this lab session?', cancel: 'Cancel this class?', help: 'A little help goes a long way.' }[modal]}</h2>
      <p>{{ logout: 'You’re about to sign out of fafo. Your attendance and progress are safely saved.', end: 'Students will no longer be able to use this code. All existing attendance records will be kept.', cancel: 'The session will be cancelled and every enrolled student will be notified. It will not count towards attendance statistics.', help: isTeacher ? 'Create a lab session to generate a 6-character code, then share it with your class. Check-ins appear live on the Active Sessions page.' : 'Ask your teacher for the 6-character code during the lab and enter it on Submit Attendance. For support, contact attendance@university.edu.' }[modal]}</p>
      <div className="modal-actions"><Button onClick={() => setModal(null)}>{modal === 'help' ? 'Got it' : 'Back'}</Button>{modal !== 'help' && <Button variant={modal === 'logout' ? 'primary' : 'danger'} onClick={() => { if (modal === 'logout') onLogout(); else endSession(modal); setModal(null) }}>{modal === 'logout' ? 'Sign out' : modal === 'end' ? 'End session' : 'Cancel class'}</Button>}</div></section></div>}
    {courseDetail && <div className="modal-backdrop" onClick={() => setCourseDetail(null)}><section className="modal course-detail" role="dialog" aria-modal="true" aria-label="Course details" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close course" onClick={() => setCourseDetail(null)}><X size={19} /></button><CourseIcon index={sections.indexOf(courseDetail)} size={24} /><h2>{courseDetail.name}</h2><p>{courseDetail.professor_name}</p><div className="course-detail-stat">{isTeacher ? <><strong>{courseDetail.enrolled_students}</strong><span>students enrolled · {courseDetail.total_sessions} labs held</span></> : <><strong>{courseDetail.attended_sessions ?? 0} / {courseDetail.total_sessions}</strong><span>labs attended this semester</span></>}</div><Button variant="primary" onClick={() => { if (!isTeacher) setHistoryCourse(courseDetail.name); navigate(isTeacher ? 'Create Lab Session' : 'Attendance History') }}>{isTeacher ? 'Create lab session' : 'View attendance history'}<ArrowRight size={16} /></Button></section></div>}
  </div>
}

function EmptyState({ icon, title, text, children }: { icon: ReactNode; title: string; text: string; children?: ReactNode }) {
  return <section className="panel"><div className="empty-state">{icon}<h3>{title}</h3><p>{text}</p>{children}</div></section>
}

function CourseIcon({ index, size = 22 }: { index: number; size?: number }) {
  const Icon = ICONS[Math.max(index, 0) % ICONS.length]
  return <span className={`course-icon ${COLORS[Math.max(index, 0) % COLORS.length]}`}><Icon size={size} strokeWidth={1.7} /></span>
}

function CourseCards({ sections, isTeacher, onOpen }: { sections: Section[]; isTeacher: boolean; onOpen: (s: Section) => void }) {
  if (sections.length === 0) return <EmptyState icon={<BookOpen size={28} />} title="No courses yet" text={isTeacher ? 'Create your first course section below.' : 'Ask your teacher to enroll you in a course.'} />
  return <div className="courses-grid">{sections.map((c, i) => {
    const color = COLORS[i % COLORS.length]
    const attended = c.attended_sessions ?? 0
    const pct = c.total_sessions ? Math.round(attended / c.total_sessions * 100) : 0
    return <button className="course-card" key={c.id} onClick={() => onOpen(c)}><div className="course-top"><CourseIcon index={i} /><span className="course-code">{c.professor_name}</span><ChevronRight size={16} /></div><h3>{c.name}</h3>
      {isTeacher
        ? <><div className="course-progress-label"><span>{c.total_sessions}<span className="muted"> labs held</span></span><strong>{c.enrolled_students}</strong></div><div className={`progress-track ${color}`}><span style={{ width: c.enrolled_students ? '100%' : '0%' }} /></div></>
        : <><div className="course-progress-label"><span>{attended}<span className="muted"> / {c.total_sessions} labs attended</span></span><strong>{pct}%</strong></div><div className={`progress-track ${color}`}><span style={{ width: `${pct}%` }} /></div></>}
      <div className="course-footer"><span><span className="mini-dot" />{isTeacher ? `${c.enrolled_students} students enrolled` : pct >= 75 || c.total_sessions === 0 ? 'On track' : 'Below 75%'}</span><span>View course <ArrowUpRight size={12} /></span></div></button>
  })}</div>
}

/* ---------------------------------- Student --------------------------------- */

function AttendanceForm({ onConfirmed, notify, autoFocus = false }: { onConfirmed: () => void; notify: (m: string) => void; autoFocus?: boolean }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus])

  async function submit(e: FormEvent) {
    e.preventDefault(); setError(''); setSuccess('')
    const value = code.trim().toUpperCase()
    if (!value) { setError('Please enter the attendance code provided by your teacher.'); return }
    if (value.length < 6) { setError('Attendance codes have 6 characters.'); return }
    setLoading(true)
    try {
      let result
      try {
        result = await api.confirm(value)
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 422 && err.message.startsWith('Location is required'))) throw err
        result = await api.confirm(value, await currentPosition())
      }
      setCode('')
      setSuccess(`Attendance recorded! ${result.section_name}${result.title ? ` · ${result.title}` : ''} · ${formatTime(result.confirmed_at)}`)
      notify('Attendance recorded successfully!')
      onConfirmed()
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      setError({ 400: 'Invalid attendance code. Please check the code and try again.', 403: message(err) === 'Student is not enrolled' ? 'You are not enrolled in this course.' : message(err), 404: 'This code is no longer active. Please ask your teacher for a new code.', 409: 'You have already recorded attendance for this session.', 410: 'This code has expired. Please ask your teacher for a new code.' }[status] ?? message(err))
    } finally {
      setLoading(false)
    }
  }

  return <section className={`attendance-card ${success ? 'has-success' : ''}`}>
    <div className="attendance-decoration"><div /><div /><div /></div>
    <div className="eyebrow"><span className="icon-chip"><CheckCheck size={19} /></span> YOUR PRESENCE MATTERS</div>
    <h2>Here for the lab? Make it official.</h2>
    <p>Enter the unique code provided by your teacher during the lab.</p>
    <form onSubmit={submit} className="code-form"><div className="code-input"><Hash size={19} /><input ref={ref} value={code} onChange={e => { setCode(e.target.value.toUpperCase().replace(/\s/g, '')); setError('') }} placeholder="Enter the attendance code" maxLength={6} aria-label="Attendance code" autoComplete="off" /></div><Button type="submit" variant="primary" disabled={loading}>{loading ? <LoaderCircle className="spin" size={17} /> : <><span>Submit Attendance</span><ArrowRight size={17} /></>}</Button></form>
    {error ? <div className="form-error" role="alert"><XCircle size={15} />{error}</div> : success ? <div className="attendance-success" role="status"><CheckCircle2 size={15} />{success}</div> : <div className="secure-note"><ShieldCheck size={14} />Secure, quick, and just one code away.</div>}
  </section>
}

function StudentDashboard({ sections, stats, openSessions, now, onConfirmed, notify, navigate, onOpenCourse }: { sections: Section[]; stats: StudentStats | null; openSessions: SessionSummary[]; now: number; onConfirmed: () => void; notify: (m: string) => void; navigate: (p: string) => void; onOpenCourse: (s: Section) => void }) {
  const pct = Math.round(stats?.attendance_percentage ?? 0)
  const onTrack = !stats || stats.total_sessions === 0 || pct >= 75
  const cards = [
    { label: 'Total Labs', value: `${stats?.total_sessions ?? 0}`, icon: BookOpen, color: 'purple', note: `Across ${sections.length} enrolled course${sections.length === 1 ? '' : 's'}` },
    { label: 'Attended Labs', value: `${stats?.attended_sessions ?? 0}`, icon: CheckCircle2, color: 'green', note: 'Keep up the great work' },
    { label: 'Missed Labs', value: `${stats?.absent ?? 0}`, icon: XCircle, color: 'orange', note: 'A fresh start every day' },
    { label: 'Attendance Rate', value: `${pct}%`, icon: ArrowUpRight, color: 'blue', note: onTrack ? 'Meets 75% requirement' : 'Below 75% requirement' },
  ]
  return <>
    <StatGrid cards={cards} />
    <div className="attendance-row"><AttendanceForm onConfirmed={onConfirmed} notify={notify} />
      <section className="panel semester-progress"><div className="section-head"><h3>{onTrack ? "You're on track" : 'Time to catch up'}</h3><span className="icon-chip green"><ArrowUpRight size={16} /></span></div><div className="ring-layout"><div className="progress-ring"><svg viewBox="0 0 110 110"><circle className="ring-bg" cx="55" cy="55" r="46" /><circle className="ring-fill" cx="55" cy="55" r="46" strokeDasharray={`${pct * 2.89} 289`} /></svg><div><strong>{pct}<span>%</span></strong><small>Overall attendance</small></div></div><div className="ring-caption"><strong>{onTrack ? 'A strong semester.' : 'Every lab counts.'}</strong><p>{onTrack ? <>You're above the minimum<br />attendance requirement.</> : <>You're below the minimum<br />attendance requirement.</>}</p><span><span className="mini-dot" />75% minimum</span></div></div><div className="semester-foot"><Sparkles size={14} />Consistency looks good on you.</div></section></div>
    <div className="section-head courses-heading"><div><h2>My Courses <span className="count-badge">{sections.length}</span></h2><p>A little progress, every single lab.</p></div><button className="text-link" onClick={() => navigate('My Courses')}>View all courses <ArrowRight size={14} /></button></div>
    <CourseCards sections={sections} isTeacher={false} onOpen={onOpenCourse} />
    <div className="bottom-grid"><HistoryTable stats={stats} sections={sections} onViewAll={() => navigate('Attendance History')} />
      <section className="panel upcoming"><div className="section-head"><h2>Open Now</h2><CalendarDays size={17} /></div><div className="upcoming-date">TODAY, {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()}</div>
        {openSessions.length === 0 ? <p className="muted">No attendance sessions are open right now.</p> : openSessions.map(s => {
          const left = Math.max(0, Math.floor((Date.parse(s.expires_at) - now) / 1000))
          return <div className="upcoming-lab" key={s.id}><span className="lab-time">{formatTime(s.starts_at)}</span><div><h3>{s.section_name}</h3><p>{s.title || 'Lab session'}</p><div className="lab-meta"><span><Clock3 size={12} />{Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')} left</span>{s.location_required && <span><MapPin size={12} />Location check</span>}</div><Badge color="green">Open</Badge></div></div>
        })}
        <div className="schedule-note"><CalendarDays size={14} />All times in your local timezone.</div></section></div>
  </>
}

function HistoryTable({ stats, sections, full = false, onViewAll, initialCourse = 'All courses', notify }: { stats: StudentStats | null; sections: Section[]; full?: boolean; onViewAll?: () => void; initialCourse?: string; notify?: (m: string) => void }) {
  const [search, setSearch] = useState('')
  const [course, setCourse] = useState(initialCourse)
  const [status, setStatus] = useState('All statuses')
  const [date, setDate] = useState('')
  const rows = stats?.by_session ?? []
  const filtered = rows.filter(r => `${r.section_name} ${r.title ?? ''}`.toLowerCase().includes(search.toLowerCase()) && (course === 'All courses' || r.section_name === course) && (status === 'All statuses' || r.status === status.toLowerCase()) && (!date || r.date === date))
  function exportCsv() {
    downloadCsv('fafo-attendance.csv', [['Course', 'Lab', 'Lab Date', 'Status', 'Submission Time'], ...filtered.map(r => [r.section_name, r.title ?? '', r.date, capitalize(r.status), r.confirmed_at ? formatTime(r.confirmed_at) : '—'])])
    notify?.('Attendance records exported.')
  }
  return <section className="panel history-panel"><div className="section-head"><div><h2>{full ? 'Attendance History' : 'Recent Attendance'}</h2>{full && <p>Every lab, every check-in. All in one place.</p>}</div>{!full ? <button className="text-link" onClick={onViewAll}>View all history <ArrowRight size={14} /></button> : <Button onClick={exportCsv}><ArrowDownToLine size={15} />Export CSV</Button>}</div>
    {full && <div className="filter-bar"><label className="search-field"><Search size={16} /><input placeholder="Search courses or labs..." value={search} onChange={e => setSearch(e.target.value)} /></label><select aria-label="Filter course" value={course} onChange={e => setCourse(e.target.value)}><option>All courses</option>{sections.map(c => <option key={c.id}>{c.name}</option>)}</select><select aria-label="Filter status" value={status} onChange={e => setStatus(e.target.value)}><option>All statuses</option><option>Present</option><option>Late</option><option>Absent</option></select><input type="date" aria-label="Filter date" value={date} onChange={e => setDate(e.target.value)} /></div>}
    <div className="table-scroll"><table><thead><tr><th>Course name</th><th>Lab date</th>{full && <th>Lab time</th>}<th>Status</th><th>Submission time</th></tr></thead><tbody>{(full ? filtered : rows.slice(0, 4)).map(r => <tr key={r.session_id}><td><span className="table-course">{r.section_name}</span>{full && r.title && <small>{r.title}</small>}</td><td>{formatDate(r.date)}</td>{full && <td>{formatTime(r.starts_at)}</td>}<td><Badge color={statusColor(r.status)}>{capitalize(r.status)}</Badge></td><td>{r.confirmed_at ? formatTime(r.confirmed_at) : '—'}</td></tr>)}</tbody></table>
      {(full ? filtered : rows).length === 0 && <div className="empty-state"><Search size={28} /><h3>No attendance found</h3><p>{rows.length ? 'Try a different search or clear your filters.' : 'Your lab check-ins will appear here.'}</p>{full && rows.length > 0 && <Button onClick={() => { setSearch(''); setCourse('All courses'); setStatus('All statuses'); setDate('') }}>Clear filters</Button>}</div>}</div>
    {!full && <div className="table-note"><span className="mini-dot" />Your attendance is up to date.<span>Last synced just now</span></div>}
  </section>
}

/* ---------------------------------- Teacher --------------------------------- */

function StatGrid({ cards }: { cards: { label: string; value: string; icon: typeof Cpu; color: string; note: string }[] }) {
  return <div className="stats-grid">{cards.map(stat => <section className="stat-card" key={stat.label}><div className="stat-top"><span>{stat.label}</span><span className={`stat-icon ${stat.color}`}><stat.icon size={18} /></span></div><strong className="stat-value">{stat.value}</strong><span className="stat-note">{stat.note}</span></section>)}</div>
}

function TeacherDashboard({ sections, sessions, activeSession, credentials, setCredentials, now, navigate, notify, onEnd }: { sections: Section[]; sessions: SessionSummary[]; activeSession: SessionSummary | null; credentials: Record<string, SessionCredentials>; setCredentials: React.Dispatch<React.SetStateAction<Record<string, SessionCredentials>>>; now: number; navigate: (p: string) => void; notify: (m: string) => void; onEnd: (m: Modal) => void }) {
  const held = sessions.filter(s => s.status !== 'cancelled')
  const possible = held.reduce((sum, s) => sum + s.enrolled_count, 0)
  const confirmed = held.reduce((sum, s) => sum + s.confirmed_count, 0)
  const cards = [
    { label: 'Total Courses', value: `${sections.length}`, icon: BookOpen, color: 'purple', note: 'This semester' },
    { label: 'Enrollments', value: `${sections.reduce((sum, s) => sum + s.enrolled_students, 0)}`, icon: Users, color: 'blue', note: 'Across all courses' },
    { label: 'Active Lab Sessions', value: `${sessions.filter(s => s.status === 'open').length}`, icon: FlaskConical, color: 'orange', note: 'Ready for check-ins' },
    { label: 'Average Attendance', value: possible ? `${Math.round(confirmed / possible * 100)}%` : '—', icon: CheckCircle2, color: 'green', note: `Over ${held.length} lab session${held.length === 1 ? '' : 's'}` },
  ]
  return <>
    <StatGrid cards={cards} />
    <div className="teacher-overview"><section className="teacher-welcome panel"><span className="course-icon purple"><FlaskConical size={26} /></span><h2>A great lab starts here.</h2><p>Create a session, share a code, and let us take care of attendance.</p><Button variant="primary" onClick={() => navigate('Create Lab Session')}><Plus size={17} />Create lab session</Button></section>
      {activeSession ? <SessionDisplay session={activeSession} credentials={credentials[activeSession.id]} setCredentials={setCredentials} now={now} notify={notify} onEnd={onEnd} /> : <EmptyState icon={<FlaskConical size={28} />} title="No live session" text="Your next lab session will appear here." />}</div>
    {activeSession && <LiveTable session={activeSession} live />}
  </>
}

function SessionDisplay({ session, credentials, setCredentials, now, notify, onEnd }: { session: SessionSummary; credentials?: SessionCredentials; setCredentials: React.Dispatch<React.SetStateAction<Record<string, SessionCredentials>>>; now: number; notify: (m: string) => void; onEnd: (m: Modal) => void }) {
  const [busy, setBusy] = useState(false)
  const remaining = Math.max(0, Math.floor((Date.parse(session.expires_at) - now) / 1000))
  async function newCode() {
    setBusy(true)
    try {
      const creds = await api.rotateCode(session.id)
      setCredentials(c => ({ ...c, [session.id]: creds }))
      notify('A new attendance code is live. The previous code no longer works.')
    } catch (err) { notify(message(err)) } finally { setBusy(false) }
  }
  return <section className="panel session-display"><div className="section-head"><span className="eyebrow">LIVE LAB SESSION</span><Badge color="green">Active now</Badge></div>
    <p>{session.section_name} · started {formatTime(session.starts_at)}{session.location_required && <> · <MapPin size={12} /> location check</>}</p><h2>{session.title || 'Lab session'}</h2>
    <div className="large-code">{credentials?.code ?? '••••••'}</div>
    <div className="code-countdown"><Clock3 size={17} />{`Code expires in ${Math.floor(remaining / 60).toString().padStart(2, '0')}:${(remaining % 60).toString().padStart(2, '0')}`}</div>
    <div className="session-count"><Users size={18} /><strong>{session.confirmed_count} / {session.enrolled_count}</strong> students checked in</div>
    <div className="session-actions">
      {credentials ? <Button onClick={async () => { try { await navigator.clipboard.writeText(credentials.code); notify('Attendance code copied to clipboard.') } catch { notify(`Attendance code: ${credentials.code}`) } }}><Copy size={16} />Copy code</Button> : null}
      <Button disabled={busy} onClick={newCode}>{busy ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}{credentials ? 'New code' : 'Show code'}</Button>
      <Button variant="danger" onClick={() => onEnd('end')}>End session</Button>
      <Button onClick={() => onEnd('cancel')}><XCircle size={16} />Cancel class</Button>
    </div>
    {!credentials && <p className="muted">Codes are never stored. Click “Show code” to issue a fresh one for this session.</p>}
  </section>
}

function LiveTable({ session, live = false }: { session: SessionSummary; live?: boolean }) {
  const [rows, setRows] = useState<RecordRow[]>([])
  const [search, setSearch] = useState('')
  useEffect(() => {
    let cancelled = false
    const load = () => api.sessionRecords(session.id).then(r => { if (!cancelled) setRows(r) }).catch(() => {})
    load()
    if (!live) return () => { cancelled = true }
    const id = setInterval(load, 4000)
    return () => { cancelled = true; clearInterval(id) }
  }, [session.id, live, session.confirmed_count])
  const filtered = rows.filter(r => `${fullName(r)} ${r.email}`.toLowerCase().includes(search.toLowerCase()))
  function exportCsv() {
    downloadCsv(`attendance-${session.section_name}-${session.session_date}.csv`.replace(/\s+/g, '-'), [['Student Full Name', 'Email', 'Submission Time', 'Status', 'Method'], ...rows.map(r => [fullName(r), r.email, new Date(r.confirmed_at).toLocaleString(), capitalize(r.status), r.confirmation_method])])
  }
  return <section className="panel"><div className="section-head"><div><h2>{live ? 'Live Attendance' : 'Attendance'} {live && <span className="live-dot" />}</h2><p>{session.section_name} · {session.title || 'Lab session'} · {formatDate(session.session_date)}</p></div><Button onClick={exportCsv}><ArrowDownToLine size={15} />Export CSV</Button></div>
    {rows.length > 5 && <div className="filter-bar"><label className="search-field"><Search size={16} /><input placeholder="Search students..." value={search} onChange={e => setSearch(e.target.value)} /></label></div>}
    <div className="table-scroll"><table><thead><tr><th>Student full name</th><th>Email</th><th>Submission time</th><th>Status</th></tr></thead><tbody>{filtered.map((s, i) => <tr key={s.id}><td><div className="student-cell"><span className={`avatar small ${COLORS[i % COLORS.length]}`}>{initials(s)}</span><strong>{fullName(s)}</strong></div></td><td>{s.email}</td><td>{formatTime(s.confirmed_at)}</td><td><Badge color={statusColor(s.status)}>{capitalize(s.status)}</Badge></td></tr>)}</tbody></table>
      {rows.length === 0 && <div className="empty-state"><Users size={28} /><h3>No check-ins yet</h3><p>{live ? 'Student check-ins will appear here as they happen.' : 'Nobody confirmed attendance for this session.'}</p></div>}</div></section>
}

function CreateSessionForm({ sections, notify, onCreated }: { sections: Section[]; notify: (m: string) => void; onCreated: (c: SessionCredentials) => Promise<void> }) {
  const [sectionId, setSectionId] = useState('')
  const [title, setTitle] = useState('')
  const [duration, setDuration] = useState('15')
  const [useLocation, setUseLocation] = useState(false)
  const [radius, setRadius] = useState('100')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const selected = sectionId || sections[0]?.id || ''

  async function submit(e: FormEvent) {
    e.preventDefault(); setError('')
    if (!selected) { setError('Create a course section first.'); return }
    setLoading(true)
    try {
      const location = useLocation ? await currentPosition() : null
      const creds = await api.openSession({
        section_id: selected,
        title: title.trim() || undefined,
        duration_minutes: Number(duration),
        ...(location ? { location_latitude: location.latitude, location_longitude: location.longitude, allowed_radius_meters: Number(radius) } : {}),
      })
      notify('Lab session created. Your attendance code is live.')
      await onCreated(creds)
    } catch (err) { setError(message(err)) } finally { setLoading(false) }
  }

  return <div className="create-layout"><form className="panel session-form" onSubmit={submit}><div className="section-head"><div><h2>Session details</h2><p>Start a lab now and generate a unique attendance code.</p></div><span className="course-icon purple"><FlaskConical size={21} /></span></div>
    <label>Course<select value={selected} onChange={e => setSectionId(e.target.value)}>{sections.length === 0 && <option value="">No courses yet</option>}{sections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    <label>Lab title<input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Sequential Logic & Flip-Flops" maxLength={200} /></label>
    <label>Attendance code expires after<select value={duration} onChange={e => setDuration(e.target.value)}><option value="5">5 minutes</option><option value="10">10 minutes</option><option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">60 minutes</option><option value="120">120 minutes</option></select></label>
    <label className="setting-row"><span><strong>Require students to be nearby</strong><br /><small className="muted">Uses your current location as the classroom position.</small></span><input type="checkbox" checked={useLocation} onChange={e => setUseLocation(e.target.checked)} /></label>
    {useLocation && <label>Allowed radius<select value={radius} onChange={e => setRadius(e.target.value)}><option value="50">50 meters</option><option value="100">100 meters</option><option value="250">250 meters</option><option value="500">500 meters</option></select></label>}
    <div className="info-note"><ShieldCheck size={18} /><p>A unique 6-character code will be generated and activated. Share it with students in your classroom.</p></div>
    {error && <p className="form-error">{error}</p>}
    <Button type="submit" variant="primary" disabled={loading}>{loading ? <LoaderCircle size={16} className="spin" /> : <><Plus size={17} />Generate code & start session</>}</Button></form>
    <section className="session-tips"><div className="tip-art"><FlaskConical size={64} strokeWidth={1} /><span><Check size={23} /></span></div><h2>Less paperwork.<br />More possibility.</h2><p>Your session code makes checking in simple, so you can focus on what matters: teaching.</p><div><CheckCircle2 size={16} />Unique code for every session</div><div><CheckCircle2 size={16} />Real-time student submissions</div><div><CheckCircle2 size={16} />Easy CSV exports</div></section></div>
}

function CreateSectionForm({ notify, onCreated }: { notify: (m: string) => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('')
    try { await api.createSection(name.trim()); setName(''); notify('Course section created.'); onCreated() } catch (err) { setError(message(err)) }
  }
  return <form className="panel session-form" onSubmit={submit} style={{ marginTop: 20 }}><div className="section-head"><div><h2>New course section</h2><p>Add a course, then enroll students from the Students page.</p></div></div>
    <label>Course name<input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Digital Electronics" required maxLength={200} /></label>
    {error && <p className="form-error">{error}</p>}
    <Button type="submit" variant="primary"><Plus size={16} />Create course</Button></form>
}

function RecordsPage({ sessions }: { sessions: SessionSummary[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [stats, setStats] = useState<ClassStats | null>(null)
  const selected = sessions.find(s => s.id === selectedId) ?? null
  useEffect(() => { if (selected) api.classStats(selected.section_id).then(setStats).catch(() => setStats(null)) }, [selected])
  if (sessions.length === 0) return <EmptyState icon={<History size={28} />} title="No sessions yet" text="Attendance records appear here after your first lab session." />
  return <>
    <section className="panel"><div className="section-head"><div><h2>Lab sessions</h2><p>Select a session to see who checked in.</p></div></div>
      <div className="table-scroll"><table><thead><tr><th>Course</th><th>Lab</th><th>Date</th><th>Checked in</th><th>Status</th></tr></thead><tbody>{sessions.map(s => <tr key={s.id} onClick={() => setSelectedId(s.id)} style={{ cursor: 'pointer' }} className={s.id === selectedId ? 'selected' : ''}><td><span className="table-course">{s.section_name}</span></td><td>{s.title || '—'}</td><td>{formatDate(s.starts_at)} · {formatTime(s.starts_at)}</td><td>{s.confirmed_count} / {s.enrolled_count}</td><td><Badge color={s.status === 'open' ? 'green' : s.status === 'cancelled' ? 'red' : 'purple'}>{capitalize(s.status)}</Badge></td></tr>)}</tbody></table></div></section>
    {selected && <><div className="section-spacer" />{stats && <p className="muted">{selected.section_name}: {Math.round(stats.attendance_percentage)}% average attendance over {stats.total_sessions} sessions.</p>}<LiveTable session={selected} live={selected.status === 'open'} /></>}
  </>
}

function StudentsPage({ sections, notify, onChanged }: { sections: Section[]; notify: (m: string) => void; onChanged: () => void }) {
  const [sectionId, setSectionId] = useState('')
  const [students, setStudents] = useState<User[]>([])
  const [search, setSearch] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const selected = sectionId || sections[0]?.id || ''
  const load = useCallback(() => { if (selected) api.sectionStudents(selected).then(setStudents).catch(err => setError(message(err))) }, [selected])
  useEffect(() => { load() }, [load])
  async function enroll(e: FormEvent) {
    e.preventDefault(); setError('')
    try { await api.enroll(selected, email.trim()); setEmail(''); notify('Student enrolled.'); load(); onChanged() } catch (err) { setError(message(err)) }
  }
  if (sections.length === 0) return <EmptyState icon={<Users size={28} />} title="No courses yet" text="Create a course section on My Courses before enrolling students." />
  const sectionName = sections.find(s => s.id === selected)?.name
  return <section className="panel"><div className="section-head"><div><h2>Enrolled Students</h2><p>{sectionName}</p></div><Badge color="purple">{students.length} students</Badge></div>
    <div className="filter-bar"><select aria-label="Course" value={selected} onChange={e => setSectionId(e.target.value)}>{sections.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select><label className="search-field"><Search size={16} /><input placeholder="Find a student..." value={search} onChange={e => setSearch(e.target.value)} /></label></div>
    <form className="filter-bar" onSubmit={enroll}><label className="search-field"><UserRound size={16} /><input type="email" placeholder="Student email to enroll" value={email} onChange={e => setEmail(e.target.value)} required /></label><Button type="submit" variant="primary"><Plus size={15} />Enroll</Button></form>
    {error && <p className="form-error">{error}</p>}
    <div className="student-directory">{students.filter(s => `${fullName(s)} ${s.email}`.toLowerCase().includes(search.toLowerCase())).map((s, i) => <div key={s.id}><span className={`avatar ${COLORS[i % COLORS.length]}`}>{initials(s)}</span><span><strong>{fullName(s)}</strong><small>{s.email}</small></span><Badge>Enrolled</Badge></div>)}</div>
    {students.length === 0 && <div className="empty-state"><Users size={28} /><h3>No students yet</h3><p>Enroll students by their registered email address.</p></div>}
  </section>
}

/* ---------------------------------- Shared ---------------------------------- */

function ProfilePanel({ user }: { user: User }) {
  const isTeacher = user.role === 'professor'
  return <section className="panel profile-panel"><div className="profile-cover"><GraduationCap size={100} strokeWidth={0.7} /></div><div className="profile-intro"><span className="avatar profile-avatar">{initials(user)}</span><div><h2>{fullName(user)}</h2><p>{user.email}</p></div><Badge color="purple">{isTeacher ? 'Teacher' : 'Student'}</Badge></div>
    <div className="profile-form"><h3>Personal information</h3><div className="form-two"><label>First name<input value={user.first_name} readOnly /></label><label>Last name<input value={user.last_name} readOnly /></label></div><label>University email<input type="email" value={user.email} readOnly /></label><label>Account ID<input value={user.id} readOnly /></label></div></section>
}

function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [role, setRole] = useState<'student' | 'professor'>('student')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function run(action: () => Promise<User>) {
    setError(''); setLoading(true)
    try { onLogin(await action()) } catch (err) { setError(err instanceof ApiError && err.status === 401 ? 'Incorrect email or password.' : message(err)) } finally { setLoading(false) }
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    if (!email.trim() || !password) { setError('Please complete all fields to continue.'); return }
    if (mode === 'login') return run(() => api.login(email.trim(), password))
    if (!firstName.trim() || !lastName.trim()) { setError('Please enter your first and last name.'); return }
    if (password.length < 12) { setError('Your password must contain at least 12 characters.'); return }
    run(async () => { await api.register({ email: email.trim(), password, first_name: firstName.trim(), last_name: lastName.trim(), role }); return api.login(email.trim(), password) })
  }

  return <div className="login-page"><div className="login-story"><a href="#" className="brand"><span className="brand-icon"><GraduationCap size={26} /></span><span>fafo.</span></a><div className="login-story-content"><span className="login-eyebrow">SHOW UP. STAND OUT.</span><h1>Your presence.<br />Your potential.<br /><span>Connected.</span></h1><p>Less time taking attendance.<br />More time making a difference.</p><div className="login-art"><div className="art-ring ring-one" /><div className="art-ring ring-two" /><div className="art-ring ring-three" /><div className="floating-check"><CheckCheck size={42} /></div><div className="floating-card"><span><CheckCircle2 size={22} /></span><div><strong>You're all checked in.</strong><small>One less thing to think about.</small></div></div></div></div><span className="login-copyright">Built for the next generation of possibility.</span></div>
    <div className="login-form-side"><span className="login-top-note">Your campus, connected.</span><div className="login-box">
      <span className="login-mini-icon"><GraduationCap size={27} /></span><h1>Welcome to fafo.</h1><p>A little presence makes a big difference.</p>
      <div className="role-tabs"><button className={mode === 'login' ? 'active' : ''} onClick={() => { setMode('login'); setError('') }}><UserRound size={17} />Sign in</button><button className={mode === 'register' ? 'active' : ''} onClick={() => { setMode('register'); setError('') }}><Plus size={17} />Create account</button></div>
      <form onSubmit={submit} noValidate>
        {mode === 'register' && <><div className="form-two"><label>First name<input autoComplete="given-name" value={firstName} onChange={e => setFirstName(e.target.value)} placeholder="Alex" /></label><label>Last name<input autoComplete="family-name" value={lastName} onChange={e => setLastName(e.target.value)} placeholder="Popescu" /></label></div>
          <label>I am a<select value={role} onChange={e => setRole(e.target.value as 'student' | 'professor')}><option value="student">Student</option><option value="professor">Teacher</option></select></label></>}
        <label>University email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@university.edu" /></label>
        <label><span className="password-label">Password</span><div className="password-field"><input type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={e => setPassword(e.target.value)} placeholder={mode === 'login' ? 'Enter your password' : 'At least 12 characters'} /><button type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></label>
        {error && <div className="form-error" role="alert"><XCircle size={15} />{error}</div>}
        <Button variant="primary" type="submit" disabled={loading}>{loading ? <LoaderCircle size={18} className="spin" /> : <>{mode === 'login' ? 'Log in to your workspace' : 'Create account'}<ArrowRight size={17} /></>}</Button>
      </form>
      <div className="demo-login"><span>JUST EXPLORING?</span><button disabled={loading} onClick={() => run(() => api.login(DEMO_ACCOUNTS.student, DEMO_PASSWORD))}>Student demo <ArrowUpRight size={14} /></button><button disabled={loading} onClick={() => run(() => api.login(DEMO_ACCOUNTS.professor, DEMO_PASSWORD))}>Teacher demo <ArrowUpRight size={14} /></button></div>
      <div className="login-secure"><ShieldCheck size={15} />Your university. Your data. Always secure.</div>
    </div><span className="login-bottom">Having trouble? <a href="mailto:attendance@university.edu">Contact university support</a></span></div></div>
}

export default App
