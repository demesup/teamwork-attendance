# Teamwork Attendance – System Remediation and Implementation Plan

## Scope

This plan covers the current merged frontend/API stack:

- Frontend: `http://localhost:8080`
- API: `http://localhost:8000`
- Backend: FastAPI, SQLAlchemy, PostgreSQL/SQLite
- Frontend: React, Vite, TypeScript

## Executive Summary

The application has a working frontend and backend, but several security, correctness, deployment, and testing issues remain. The highest-priority issues are professor self-registration, replayable QR tokens, unsafe secrets and demo credentials, incorrect attendance statistics, incomplete absence handling, browser token storage, and the lack of automated frontend/API contract tests.

## Priority 0 – Critical Security Issues

### 1. Public users can create professor accounts

**Affected files:** `app/main.py`, `frontend/src/App.tsx`

The registration page allows a user to select the professor role, and the backend accepts `role="professor"` without administrator approval.

**Risk:** Anyone can create a professor account, create sections, enroll students, open attendance sessions, and view attendance records.

**Fix:** Allow public registration only for students, or require administrator invitation/approval and verified university email for professors. Enforce this in the backend, not only in the frontend.

### 2. QR tokens can be replayed

**Affected file:** `app/main.py`

The current QR confirmation route validates the token signature, session ID, code version, and expiry, but does not consistently consume and store the token nonce.

**Risk:** The same QR token can be reused by multiple students until it expires.

**Fix:** Extract the nonce, validate it, store it on `AttendanceRecord`, add a unique database constraint, and reject already-used nonces. Make the operation safe under concurrent requests.

**Tests:** Cover first use, reuse by the same student, reuse by another student, expiry, another session, and code rotation.

### 3. Default secrets and demo credentials are unsafe

**Affected files:** `app/config.py`, `docker-compose.yml`, `.env.example`, `app/seed.py`

Docker Compose uses predictable JWT/code secrets and enables demo data with a known password.

**Risk:** Tokens or attendance codes may be forged, and demo accounts may remain available in a real deployment.

**Fix:** Require strong environment-provided secrets, fail production startup when secrets are missing or weak, disable demo data by default, and use separate development and production profiles.

### 4. Authentication tokens are stored in `sessionStorage`

**Affected file:** `frontend/src/api.ts`

The frontend stores the JWT in browser-accessible storage.

**Risk:** An XSS vulnerability could expose the token and allow account impersonation.

**Fix:** Prefer secure HTTP-only cookies with `Secure` and `SameSite` attributes, CSRF protection, and a strict Content Security Policy. If storage remains temporarily, document the risk and keep token lifetimes short.

## Priority 1 – Attendance Correctness

### 5. Cancelled sessions count as absences

**Affected files:** `app/main.py`, `tests/test_absence_and_stats.py`

Student statistics include cancelled sessions and report them as absences. The test suite already documents this as an expected failure. Class statistics also include cancelled sessions.

**Fix:** Exclude cancelled sessions from total sessions, absence counts, and attendance percentages. Decide whether open, unexpired sessions should be excluded or shown as `pending`/`in_progress`. Remove the `xfail` marker after fixing the behavior.

### 6. Approved absence requests do not excuse attendance

**Affected files:** `app/main.py`, `app/models.py`, `app/schemas.py`

Approving an absence request changes only the request status. The student's statistics still show an absence.

**Fix:** Add an `excused` attendance status, create an excused record when a linked absence is approved, or make statistics include approved requests. Add an `excused` counter to student statistics and audit the change.

### 7. Attendance codes are case-sensitive

**Affected files:** `app/main.py`, `frontend/src/App.tsx`

Lowercase manually entered values can be rejected and counted as failed attempts.

**Fix:** Normalize backend input with `strip().upper()` before HMAC comparison. The backend must normalize input even though the frontend also does it.

### 8. `late` attendance is never assigned

**Affected files:** `app/models.py`, `app/main.py`, `app/config.py`

The application defines `present` and `late`, but confirmation always writes `present`.

**Fix:** Add a configurable late threshold and assign `late` when confirmation occurs after the threshold. Add a protected professor correction endpoint if manual changes are required.

### 9. Client IP handling fails behind reverse proxies

**Affected file:** `app/main.py`

The application uses `request.client.host`. Behind a proxy, all requests may appear to use the proxy IP.

**Risk:** IP-based rate limiting may lock out many students at once.

**Fix:** Configure trusted proxy addresses, enable correct proxy-header handling, and only use forwarded IP headers from trusted proxies. Add tests for trusted and untrusted proxy headers.

### 10. Session dates are timezone-sensitive

**Affected file:** `app/main.py`

The API uses local `date.today()` while timestamps are generated in UTC.

**Fix:** Choose one application timezone and derive session dates from the session start timestamp using that timezone. Apply the same rule to API responses, statistics, frontend formatting, and CSV exports.

## Priority 2 – Missing or Incomplete Functionality

### 11. Notifications cannot be marked as read

**Affected files:** `app/main.py`, `frontend/src/App.tsx`, `frontend/src/api.ts`

Notifications expose `read_at`, but there is no API or frontend action to update it.

**Fix:** Add `PATCH /api/v1/notifications/{id}/read` and optionally a bulk mark-read endpoint. Ensure users can update only their own notifications. Add frontend actions and tests.

### 12. Attachment URLs are accepted without validation

**Affected files:** `app/schemas.py`, `app/main.py`

Absence requests accept an arbitrary `attachment_url`; there is no upload service or validation.

**Fix:** Either remove attachments until implemented or add a secure upload service with file-size limits, MIME and magic-byte validation, generated file IDs, protected storage, and access-controlled downloads.

### 13. Manual attendance correction is not available

There is no clear professor endpoint for correcting an attendance record.

**Fix:** Add a protected attendance-record update endpoint for the owning professor or administrator. Support `present`, `late`, and `excused`, and create an audit log for every change.

### 14. Session expiry and error states need clearer frontend handling

The UI refreshes periodically, but users may see stale sessions and generic error messages.

**Fix:** Distinguish invalid code, expired session, closed session, cancelled session, duplicate attendance, non-enrollment, location denial, and out-of-range location. Refresh data after relevant 404, 409, and 410 responses.

## Priority 3 – Frontend and Integration

### 15. No automated frontend test suite exists

The repository contains backend tests but no frontend test runner or frontend tests.

**Fix:** Add tests for login, demo login, role-based navigation, course creation, session creation, attendance submission, expired codes, logout, mobile navigation, CSV export, and API failures. Add a browser smoke test against Docker Compose.

### 16. Frontend and backend contracts are maintained separately

The frontend uses handwritten TypeScript API types and endpoint paths independently from FastAPI schemas.

**Fix:** Generate a TypeScript client from OpenAPI or add contract tests covering every frontend-used endpoint and response shape. Run the checks in CI.

### 17. CORS configuration should be documented for split deployments

CORS is not a critical blocker in Docker because Nginx proxies `/api` and the frontend/API are same-origin on port 8080. Vite also proxies `/api` during development.

If the frontend and API are deployed on different origins, add explicit CORS configuration allowing only trusted frontend origins. Do not use wildcard origins with credentials.

## Priority 4 – Infrastructure and Architecture

### 18. Manual database schema changes should be replaced with Alembic

**Affected file:** `app/db.py`

The application uses `create_all()` and manual `ALTER TABLE` statements.

**Risk:** This does not safely support column changes, data migrations, index changes, or rollbacks.

**Fix:** Add Alembic, connect it to SQLAlchemy metadata, create versioned migration scripts, and run migrations during deployment.

### 19. Startup handler uses deprecated FastAPI events

**Affected file:** `app/main.py`

The application uses `@app.on_event("startup")`.

**Fix:** Move table initialization and schema checks into a FastAPI lifespan context using `asynccontextmanager`.

### 20. Test and fallback secrets are too short

**Affected files:** `app/config.py`, `tests/conftest.py`

Some fallback and test secrets are short strings.

**Fix:** Use randomly generated secrets of at least 32 bytes in tests, `.env.example`, development configuration, and production configuration.

### 21. Development and production Docker configurations are not separated

The Compose setup contains development credentials, demo data, and development secrets.

**Fix:** Create separate development and production Compose configurations. Production should require external secrets, disable demo seeding, keep PostgreSQL private, use migrations, add restart/resource policies, and document backups.

### 22. Missing administrator role and approval workflow

There is no clear administrator role for approving professors, managing users, reviewing audit logs, or revoking accounts.

**Fix:** Consider adding an `admin` role and workflows for professor approval, account activation/deactivation, audit review, and user management.

### 23. Academic data model is minimal

The current model contains users, sections, enrollments, attendance sessions, and attendance records.

Possible future entities include departments, courses, academic terms, student profiles, professor profiles, class meetings, rooms, and university identifiers.

This is future architecture work, not an immediate blocker.

## Existing functionality that should not be reported as missing

The merged branch already contains:

- A React frontend.
- `GET /api/v1/auth/me`.
- `GET /api/v1/sections`.
- `GET /api/v1/sections/{section_id}/students`.
- `GET /api/v1/attendance-sessions`.
- `GET /api/v1/attendance-sessions/{session_id}/records`.
- `POST /api/v1/attendance-sessions/{session_id}/close`.
- Docker frontend service on port 8080.

These should not be listed as missing endpoints in the final report.

## Recommended Implementation Order

### Phase 1 – Critical security

1. Restrict professor registration.
2. Replace default secrets.
3. Disable demo accounts by default.
4. Fix QR nonce replay.
5. Improve authentication token storage.

### Phase 2 – Attendance correctness

1. Exclude cancelled sessions from statistics.
2. Exclude unfinished sessions from absences.
3. Implement approved absence handling.
4. Normalize attendance codes.
5. Implement late attendance.
6. Fix proxy IP handling.

### Phase 3 – User experience

1. Add notification read state.
2. Improve session expiry messages.
3. Standardize timezone behavior.
4. Add manual attendance correction.
5. Validate absence attachments.

### Phase 4 – Infrastructure and testing

1. Add Alembic migrations.
2. Refactor startup to lifespan.
3. Improve production Docker configuration.
4. Add frontend tests.
5. Add API contract tests.
6. Add browser smoke tests.

### Phase 5 – Future architecture

1. Add administrator approval.
2. Add courses and departments.
3. Add student and professor profiles.
4. Add recurring class meetings.

## Verification Checklist

### Security

- Public users cannot create professor accounts.
- Production startup fails without strong secrets.
- Demo data is disabled by default.
- QR tokens can only be used once.
- Authentication tokens are not unnecessarily exposed to JavaScript.
- Proxy IP handling does not globally lock out users.

### Attendance

- Cancelled sessions do not count as absences.
- Unfinished sessions are not counted as absences.
- Approved absences appear as excused.
- Lowercase codes work correctly.
- Late attendance is recorded correctly.
- Duplicate attendance is rejected safely.

### Frontend

- Students can log in.
- Professors can log in.
- Students can see courses and active sessions.
- Professors can create and close sessions.
- Attendance records update correctly.
- Notifications can be marked as read.
- Expired and cancelled sessions show correct messages.
- Mobile navigation works.

### Infrastructure

- Frontend build passes.
- Backend tests pass.
- Frontend tests pass.
- API contract tests pass.
- Docker Compose starts successfully.
- Database migrations run successfully.
- Production configuration does not use development credentials.
