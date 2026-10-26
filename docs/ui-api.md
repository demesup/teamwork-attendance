# UI integration guide

The backend is a FastAPI service. It also serves a ready-made web UI, Roll Call,
from `app/static/` at `http://localhost:8000/`. When running with Docker:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health check: `http://localhost:8000/health`

## Login

Use `POST /api/v1/auth/register` to create a student. Creating a professor also
needs `"professor_code"`, matching the `PROFESSOR_SIGNUP_CODE` setting
(`professor-dev-only` in `docker-compose.yml`). Then call `POST /api/v1/auth/token` as form data:

```text
username=student@example.com
password=your-password
```

Send the returned token with every protected request:

```http
Authorization: Bearer <access_token>
```

Browsers can instead call `POST /api/v1/auth/login` with the same form data.
It sets an HTTP-only, `SameSite=Strict` session cookie and returns the user, so
the token is never readable by page scripts. `POST /api/v1/auth/logout` clears
it, and `GET /api/v1/users/me` returns the signed-in user. Every protected
endpoint accepts either the bearer header or the cookie.

## Discovering sections, sessions, and people

| Purpose | Endpoint |
|---|---|
| Sections I teach (professor) or am enrolled in (student), each with its `open_session` | `GET /api/v1/sections` |
| Students enrolled in a section | `GET /api/v1/sections/{section_id}/students` |
| Sessions of a section with status (`open`, `expired`, `closed`, `cancelled`) and confirmed count | `GET /api/v1/sections/{section_id}/sessions` |
| Live confirmation list for a session | `GET /api/v1/attendance-sessions/{session_id}/records` |
| End a session early (still counts in statistics) | `POST /api/v1/attendance-sessions/{session_id}/close` |
| A student's own absence requests | `GET /api/v1/absence-requests/me` |
| Mark all notifications read | `POST /api/v1/notifications/read` |
| Every student a session counts for, with status | `GET /api/v1/attendance-sessions/{session_id}/roster` |
| Correct one student's status (`present`, `late`, `excused`, `absent`) | `PUT /api/v1/attendance-sessions/{session_id}/students/{student_id}` |
| Remove a student from a class (history kept) | `DELETE /api/v1/sections/{section_id}/enrollments/{student_id}` |
| Upload / open absence evidence (PDF, PNG, JPEG, 5 MB) | `POST` / `GET /api/v1/absence-requests/{request_id}/attachment` |

Students find the session to confirm from `open_session.id` in
`GET /api/v1/sections`, and `open_session.already_confirmed` tells the UI
whether to show the code input.

## Professor UI

1. Create a section with `POST /api/v1/sections`.
2. Enroll a student with
   `POST /api/v1/sections/{section_id}/enrollments`, sending either
   `{"student_id": "..."}` or `{"email": "student@example.com"}`.
3. Open attendance with `POST /api/v1/attendance-sessions`:

```json
{
  "section_id": "section-id",
  "duration_minutes": 15,
  "location_latitude": 41.0082,
  "location_longitude": 28.9784,
  "allowed_radius_meters": 100
}
```

Set `late_after_minutes` to record confirmations after that many minutes as
`late`. Location fields are optional. If all three are provided, the student must
submit a location within the configured radius. The response contains a
temporary `code` (10 characters, without look-alikes such as 0/O or 1/I), signed
`qr_token`, and `location_required`. Display the code
or turn the token into a QR image in the UI. Do not persist either value in
browser storage.

The QR token is single-use and expires after 30 seconds. Refresh it about every
20 seconds with `POST /api/v1/attendance-sessions/{session_id}/qr-token`, which
returns a new `qr_token` and `expires_at`. The typed `code` stays valid until the
session ends or is rotated.

To invalidate a code that has been shared, call
`POST /api/v1/attendance-sessions/{session_id}/rotate-code`. It returns a new
`code` and `qr_token`, and the old code and older QR tokens stop working.

Students may send an `X-Device-Id` header with their confirmation. One device can
confirm for only one student per session. Repeated failed attempts return `429`.

## Student UI

Show a code input or QR scanner, then call:

```http
POST /api/v1/attendance-sessions/{session_id}/confirm
Authorization: Bearer <student-token>
Content-Type: application/json
```

With a code:

```json
{ "code": "TEMPORARY-CODE" }
```

Or with a scanned QR token:

```json
{
  "qr_token": "SIGNED-QR-TOKEN",
  "latitude": 41.0082,
  "longitude": 28.9784
}
```

When `location_required` is true, include latitude and longitude with either
confirmation method. Request location permission only when needed and explain
why it is used. Never trust client coordinates for anything other than the
server-side radius check.

Display success only after the API returns `200`. Handle `400` as invalid,
`403` as not enrolled or outside the location, `409` as already confirmed, used
QR, or a device already used by another student, `410` as expired, and `429` as
too many failed attempts.

## Absence requests and cancellation notifications

Students submit an absence request with `POST /api/v1/absence-requests`:

```json
{
  "section_id": "section-id",
  "attendance_session_id": "optional-session-id",
  "reason": "Medical appointment",
  "attachment_url": "https://private-file-service.example/file-id"
}
```

The API checks enrollment before creating a `pending` request. File uploads
should be handled by a private, signed-upload service; do not accept arbitrary
public URLs in production.

Professors list requests with
`GET /api/v1/sections/{section_id}/absence-requests`, then approve or reject
one with:

```http
PATCH /api/v1/absence-requests/{request_id}
Content-Type: application/json

{ "status": "approved" }
```

The allowed statuses are `approved` and `rejected`. The student receives a
notification when the request is reviewed. Approving a request that names an
`attendance_session_id` records the student as `excused` for that session
(unless they attended), and excused sessions are left out of the percentage.

Professors cancel an open session with
`POST /api/v1/attendance-sessions/{session_id}/cancel`. Every enrolled student
receives a notification. The UI can fetch notifications from
`GET /api/v1/notifications` and show the cancellation message.

## Statistics and charts

Cancelled sessions are left out of both statistics endpoints, and a session
only counts for students who were enrolled when it started. Class statistics
include finished sessions only. A session that
is still open counts for a student only once they have confirmed it, so a
student is not shown as absent while attendance is still running.

Students call `GET /api/v1/students/me/statistics`. The response is safe to
bind directly to charts:

- `attendance_percentage`: summary donut/progress chart
- `present`, `late`, `absent`: status bar or pie chart
- `by_session`: timeline with `date` and `status`

Professors call `GET /api/v1/sections/{section_id}/statistics`. Its
`by_session` array contains `confirmed`, `enrolled`, and
`attendance_percentage`, which can drive a class attendance line or bar chart.
Refresh after opening/closing a session rather than polling aggressively.

## UI security rules

- Keep access tokens in a secure, HTTP-only cookie for a browser application;
  do not put them in `localStorage`.
- Never show a student the professor's session response or another student's
  identifiers.
- Treat all API responses as untrusted text and escape user-generated fields.
- Do not log codes, QR tokens, passwords, or bearer tokens.
- The development registration endpoint must be replaced with an administrator
  or institutional-SSO flow before production.
