// Thin typed client for the FastAPI backend. Requests go to same-origin /api,
// which Vite (dev) or nginx (docker) proxies to the API service.

export type Role = 'student' | 'professor'
export type User = { id: string; email: string; first_name: string; last_name: string; role: Role }
export type Section = {
  id: string
  name: string
  professor_id: string
  professor_name: string
  enrolled_students: number
  total_sessions: number
  attended_sessions: number | null
}
export type SessionSummary = {
  id: string
  section_id: string
  section_name: string
  title: string | null
  status: 'open' | 'closed' | 'cancelled'
  session_date: string
  starts_at: string
  expires_at: string
  location_required: boolean
  confirmed_count: number
  enrolled_count: number
}
export type SessionCredentials = {
  id: string
  section_id: string
  title: string | null
  expires_at: string
  code: string
  qr_token: string
  location_required: boolean
}
export type RecordRow = {
  id: string
  student_id: string
  first_name: string
  last_name: string
  email: string
  status: string
  confirmation_method: string
  confirmed_at: string
}
export type Confirmation = {
  id: string
  session_id: string
  section_name: string
  title: string | null
  status: string
  confirmation_method: string
  confirmed_at: string
}
export type StudentSessionStat = {
  session_id: string
  section_id: string
  section_name: string
  title: string | null
  date: string
  starts_at: string
  status: 'present' | 'late' | 'absent'
  confirmed_at: string | null
}
export type StudentStats = {
  student_id: string
  total_sessions: number
  attended_sessions: number
  attendance_percentage: number
  present: number
  late: number
  absent: number
  by_session: StudentSessionStat[]
}
export type ClassStats = {
  section_id: string
  total_sessions: number
  enrolled_students: number
  total_confirmations: number
  attendance_percentage: number
  by_session: { session_id: string; date: string; confirmed: number; enrolled: number; attendance_percentage: number }[]
}
export type Notification = { id: string; title: string; message: string; read_at: string | null; created_at: string }

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

const TOKEN_KEY = 'attendance.token'
let token: string | null = readToken()
let onUnauthorized: () => void = () => {}

// sessionStorage keeps the user signed in across reloads of a tab only.
// TODO: move to an HTTP-only cookie as required by docs/ui-api.md.
function readToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}
function writeToken(value: string | null) {
  token = value
  try { value ? sessionStorage.setItem(TOKEN_KEY, value) : sessionStorage.removeItem(TOKEN_KEY) } catch { /* storage unavailable */ }
}

export const hasToken = () => token !== null
export const setUnauthorizedHandler = (handler: () => void) => { onUnauthorized = handler }

function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown[]; msg?: string }
    const field = first.loc?.[first.loc.length - 1]
    return field ? `${String(field).replace(/_/g, ' ')}: ${first.msg}` : first.msg || fallback
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown, form = false): Promise<T> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (form) {
    payload = new URLSearchParams(body as Record<string, string>)
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { method, headers, body: payload })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check that the API is running.')
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && token && !path.startsWith('/auth/token')) {
      writeToken(null)
      onUnauthorized()
    }
    throw new ApiError(response.status, errorMessage(data, `Request failed (${response.status})`))
  }
  return data as T
}

export const api = {
  async login(email: string, password: string) {
    const result = await request<{ access_token: string }>('POST', '/auth/token', { username: email, password }, true)
    writeToken(result.access_token)
    return api.me()
  },
  logout: () => writeToken(null),
  register: (body: { email: string; password: string; first_name: string; last_name: string; role: Role }) =>
    request<User>('POST', '/auth/register', body),
  me: () => request<User>('GET', '/auth/me'),

  sections: () => request<Section[]>('GET', '/sections'),
  createSection: (name: string) => request<Section>('POST', '/sections', { name }),
  sectionStudents: (sectionId: string) => request<User[]>('GET', `/sections/${sectionId}/students`),
  enroll: (sectionId: string, studentEmail: string) =>
    request<{ status: string }>('POST', `/sections/${sectionId}/enrollments`, { student_email: studentEmail }),
  classStats: (sectionId: string) => request<ClassStats>('GET', `/sections/${sectionId}/statistics`),

  sessions: (params: { open?: boolean; sectionId?: string } = {}) => {
    const query = new URLSearchParams()
    if (params.open) query.set('open', 'true')
    if (params.sectionId) query.set('section_id', params.sectionId)
    return request<SessionSummary[]>('GET', `/attendance-sessions${query.size ? `?${query}` : ''}`)
  },
  openSession: (body: {
    section_id: string
    title?: string
    duration_minutes: number
    location_latitude?: number
    location_longitude?: number
    allowed_radius_meters?: number
  }) => request<SessionCredentials>('POST', '/attendance-sessions', body),
  rotateCode: (sessionId: string) => request<SessionCredentials>('POST', `/attendance-sessions/${sessionId}/rotate-code`),
  closeSession: (sessionId: string) => request<{ status: string }>('POST', `/attendance-sessions/${sessionId}/close`),
  cancelSession: (sessionId: string) => request<{ status: string }>('POST', `/attendance-sessions/${sessionId}/cancel`),
  sessionRecords: (sessionId: string) => request<RecordRow[]>('GET', `/attendance-sessions/${sessionId}/records`),

  confirm: (code: string, location?: { latitude: number; longitude: number }) =>
    request<Confirmation>('POST', '/attendance/confirm', { code, ...location }),
  myStats: () => request<StudentStats>('GET', '/students/me/statistics'),
  notifications: () => request<Notification[]>('GET', '/notifications'),
}

export function currentPosition(): Promise<{ latitude: number; longitude: number }> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error('Location is not available in this browser.'))
    navigator.geolocation.getCurrentPosition(
      p => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
      () => reject(new Error('Location permission is needed for this session.')),
      { enableHighAccuracy: true, timeout: 10000 },
    )
  })
}
