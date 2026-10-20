# UI integration guide

The backend is a FastAPI service. When running with Docker:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health check: `http://localhost:8000/health`

## Login

Use `POST /api/v1/auth/register` to create a professor or student during
development. Then call `POST /api/v1/auth/token` as form data:

```text
username=student@example.com
password=your-password
```

Send the returned token with every protected request:

```http
Authorization: Bearer <access_token>
```

## Professor UI

1. Create a section with `POST /api/v1/sections`.
2. Enroll a student with
   `POST /api/v1/sections/{section_id}/enrollments`.
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

Location fields are optional. If all three are provided, the student must
submit a location within the configured radius. The response contains a
temporary `code`, signed `qr_token`, and `location_required`. Display the code
or turn the token into a QR image in the UI. Do not persist either value in
browser storage.

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
`403` as not enrolled, `409` as already confirmed, and `410` as expired.

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
notification when the request is reviewed.

Professors cancel an open session with
`POST /api/v1/attendance-sessions/{session_id}/cancel`. Every enrolled student
receives a notification. The UI can fetch notifications from
`GET /api/v1/notifications` and show the cancellation message.

## Statistics and charts

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

## Endpoints used by the web app

In addition to the endpoints above, the web app uses:

| Purpose | Endpoint |
|---|---|
| Current user | `GET /api/v1/auth/me` |
| List my sections (professor: owned, student: enrolled) | `GET /api/v1/sections` |
| Enrolled students of a section | `GET /api/v1/sections/{id}/students` |
| Enroll by email | `POST /api/v1/sections/{id}/enrollments` with `{ "student_email": "…" }` |
| List sessions (`?open=true`, `?section_id=`) | `GET /api/v1/attendance-sessions` |
| Live check-ins for a session | `GET /api/v1/attendance-sessions/{id}/records` |
| Issue a new code (old code and QR stop working) | `POST /api/v1/attendance-sessions/{id}/rotate-code` |
| End a session normally | `POST /api/v1/attendance-sessions/{id}/close` |
| Confirm with only the 6-character code | `POST /api/v1/attendance/confirm` with `{ "code": "…" }` |

Codes are only returned when a session is opened or its code is rotated; they are
stored hashed, so after a page reload the professor UI issues a fresh code.
