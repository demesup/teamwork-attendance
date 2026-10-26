# Problems and Technical Review

Review date: 2026-10-26

## Verification status

- The Docker Compose stack builds and runs successfully.
- The web application responds at `http://localhost:8080`.
- The API health endpoint and Swagger UI respond at `http://localhost:8000/health` and `http://localhost:8000/docs`.
- The frontend TypeScript and Vite production build succeeds.
- The backend test suite passes: 2 tests passed.
- The downloaded directory does not contain `.git` metadata, so its GitHub remote, branch, commit history, and differences from the upstream repository could not be verified.

## Comparison with `C:\Users\beldi\Downloads\plan.md`

The supplied plan does not match the current workspace exactly. Its source links point to a different directory (`D:/lab_web/teamwork-attendance`), its cited line numbers do not match this checkout, and it describes tests and implementation details that are absent here. It should therefore not be used as a current defect list without verifying each claim against this code.

### Plan findings that are valid in the current workspace

| Plan finding | Current assessment |
|---|---|
| Active sessions count as absences | **Valid.** Open/unexpired sessions are included in statistics and students without a record are labeled absent. |
| Approved absence requests do not excuse attendance | **Valid.** Approval changes only the request status and sends a notification; statistics remain unchanged. |
| `AttendanceStatus.LATE` is never assigned | **Valid.** All created records use the default `present` status. |
| No student absence-request history endpoint | **Valid missing feature.** Students can submit requests but cannot list their own requests through the API. |
| No notification read endpoint | **Valid missing feature.** Notifications can be listed, but `read_at` cannot be updated through the API. |
| No secure attachment upload service | **Valid missing feature/security concern.** The API stores an arbitrary attachment URL supplied by the client. |
| No manual professor attendance override | **Valid missing feature**, if manual correction is a product requirement. |
| Deprecated FastAPI startup event | **Valid.** The current test run emits this deprecation warning. |
| No Alembic migration tooling | **Valid.** The project uses `create_all()` and a small additive-schema helper. |
| Open professor self-registration | **Valid and high severity.** The role is accepted from the unauthenticated request. |
| Academic entities differ from the larger documented design | **Valid architectural gap**, but whether departments, courses, profiles, and recurring meetings must be implemented is a product-scope decision rather than a runtime defect. |

### Plan findings already fixed or incorrect for this workspace

| Plan finding | Evidence in the current workspace |
|---|---|
| Cancelled sessions count as absences | **Already fixed.** Both statistics queries exclude `SessionStatus.CANCELLED`. Active sessions remain a problem. |
| Typed codes are case-sensitive | **Incorrect.** `digest_code()` applies `strip().upper()`, and the tracked attendance-flow test explicitly confirms a lowercase code successfully. |
| IP rate limiting causes a proxy-wide lockout | **Not applicable.** This workspace has no IP rate limiter or `client_ip()` implementation. The actual problem is that rate limiting is absent altogether. |
| Student cannot discover sections or open sessions | **Already fixed.** `GET /api/v1/sections` returns enrolled sections, and `GET /api/v1/attendance-sessions?open=true` returns the student's open sessions. |
| Professor cannot list sections | **Already fixed.** `GET /api/v1/sections` returns the professor's sections. |
| Professor cannot list enrolled students | **Already fixed through a differently named endpoint.** `GET /api/v1/sections/{section_id}/students` provides the roster. |
| Professor cannot see attendance records | **Already fixed.** `GET /api/v1/attendance-sessions/{session_id}/records` exists. |
| Professor cannot close a session normally | **Already fixed.** `POST /api/v1/attendance-sessions/{session_id}/close` exists. |
| Current-user profile endpoint is missing | **Already fixed through a differently named endpoint.** `GET /api/v1/auth/me` exists. |
| CORS middleware is missing | **Incorrect.** `CORSMiddleware` is configured from `CORS_ORIGINS`. |
| Short keys produce hundreds of test warnings | **Not reproduced.** The current two-test suite emitted three deprecation warnings, not JWT key-length warnings. Production secret handling is still unsafe in Compose. |
| Frontend UI must be built | **Incorrect for this workspace.** A React/Vite student and professor UI is present and builds successfully. |

### Plan claims that cannot be verified in this workspace

- The referenced `tests/test_absence_and_stats.py` file and its strict `xfail` test are not present.
- The plan claims nonce-based single-use QR tokens and implemented rate limiting. This workspace uses a signed QR token containing `session_id`, `code_version`, and expiry, but has neither a per-token nonce nor rate-limit enforcement.
- The plan's proposed endpoints sometimes duplicate current capabilities under new names. API naming changes should be treated as compatibility/product decisions, not automatically as defects.

## High-severity problems

### 1. Anyone can create a professor account

**Location:** `app/main.py`, `register()`

The public registration endpoint accepts either `student` or `professor` directly from the request body. There is no email verification, administrator approval, invitation, or trusted-domain check.

**Impact:** An unauthenticated user can register as a professor and obtain professor privileges, including creating course sections, enrolling students, starting attendance sessions, viewing attendance records, and reviewing absence requests.

**Recommended fix:**

- Allow public registration to create student accounts only.
- Provision professor accounts through an administrator, invitation token, or verified institutional identity provider.
- Do not trust a client-supplied privileged role.
- Add authorization tests proving that public users cannot grant themselves professor privileges.

### 2. Docker Compose contains predictable secrets and enables demo accounts

**Location:** `docker-compose.yml`, API environment configuration

The local Compose configuration uses fixed values for `JWT_SECRET` and `CODE_SECRET`, enables demo data, and publishes the application ports on all host interfaces.

**Impact:** If this configuration is reused outside an isolated development machine, attackers could know the signing secrets and demo credentials. The service may also be reachable by other machines on the local network.

**Recommended fix:**

- Keep the current values explicitly development-only.
- Load production secrets from a secret manager or protected environment variables.
- Refuse to start in production when placeholder secrets are detected.
- Disable `SEED_DEMO_DATA` outside local development.
- Bind ports to `127.0.0.1` for a local-only Compose profile, for example `127.0.0.1:8080:80`.

## Medium-severity problems

### 3. Authentication and attendance confirmation have no rate limiting

**Locations:**

- `app/main.py`, `token()`
- `app/main.py`, `confirm_attendance_by_code()` and `confirm_attendance()`

Failed attendance-code attempts are stored, but the application does not enforce a limit, cooldown, or lockout. Login attempts are not limited either.

**Impact:** Attackers can repeatedly guess passwords or six-character attendance codes. Recording attempts without enforcing a policy does not prevent abuse.

**Recommended fix:**

- Rate-limit by account and IP address.
- Add progressive delays or temporary lockouts after repeated failures.
- Limit attendance attempts per student and session.
- Return consistent error messages that do not reveal useful account or session information.
- Add monitoring and retention rules for attempt records.

### 4. Open sessions are counted as absences

**Location:** `app/main.py`, `student_statistics()` and `class_statistics()`

Statistics include every non-cancelled session. A session can therefore count against attendance while it is still open and before its expiration time. Students who have not checked in yet are shown as absent immediately.

**Impact:** Student and class attendance percentages can be temporarily incorrect and misleading.

**Recommended fix:**

- Calculate finalized statistics from closed or expired sessions only.
- Present open sessions separately as `pending` or `in progress`.
- Add tests covering open, expired, closed, and cancelled sessions.

### 5. Historical attendance uses current enrollment instead of enrollment at session time

**Locations:**

- `app/models.py`, `Enrollment`
- `app/main.py`, `student_statistics()` and `class_statistics()`

The enrollment model has no effective start/end timestamps, and statistics use currently active enrollments. A student enrolled today may be counted as absent from earlier sessions. A student removed from a section may disappear from historical denominators even if they attended earlier sessions.

**Impact:** Historical statistics can change when current enrollment changes and may not represent who was actually enrolled when a session occurred.

**Recommended fix:**

- Store enrollment start and end timestamps.
- Determine eligibility using the session date/time.
- Alternatively, snapshot eligible students when an attendance session is opened.
- Add tests for students joining and leaving a course mid-term.

### 6. Browser access tokens are stored in `sessionStorage`

**Location:** `frontend/src/api.ts`, token storage functions

The frontend stores bearer tokens in browser `sessionStorage`. The code contains a TODO noting that an HTTP-only cookie is intended.

**Impact:** Any successful cross-site scripting attack could read and exfiltrate the token. Tokens also cannot be protected with the browser's `HttpOnly` flag.

**Recommended fix:**

- Use a `Secure`, `HttpOnly`, appropriately scoped `SameSite` cookie.
- Add CSRF protection if cookie authentication is used.
- Apply a restrictive Content Security Policy as defense in depth.
- Keep access tokens short-lived and implement a safe refresh/revocation strategy if persistent sessions are needed.

### 7. Location verification trusts client-reported coordinates

**Location:** `app/main.py`, `record_attendance()`

The server correctly calculates distance from the submitted coordinates, but those coordinates are supplied by the student's browser and can be modified or spoofed.

**Impact:** Location restrictions discourage accidental remote check-ins but do not reliably prove physical presence.

**Recommended fix:**

- Describe location checking as a weak signal, not definitive proof.
- Combine it with short-lived codes, strict attempt limits, session-specific QR codes, or trusted campus/network signals.
- Establish a privacy and retention policy for stored coordinates.

### 8. Approved absence requests do not affect attendance statistics

**Locations:**

- `app/main.py`, `review_absence()`
- `app/main.py`, `student_statistics()`

Approving an absence request changes the request status and creates a notification, but it does not create an excused attendance state or alter the student's statistics. The student continues to appear absent for the associated session.

**Impact:** The absence-approval workflow has no effect on the attendance result it is expected to correct.

**Recommended fix:**

- Define the intended meaning of an approved request: `excused`, excluded from the denominator, or counted separately.
- Prefer deriving an excused result from the approved request rather than creating a normal attendance confirmation that implies presence.
- Add an `excused` count/status to the response schema if the UI needs to display it.
- Require an associated attendance session when approval is expected to change session statistics.
- Add tests for approval, rejection, duplicate review, and requests without a session.

### 9. Attachment URLs are accepted without an upload or trust boundary

**Locations:**

- `app/schemas.py`, `AbsenceRequestCreate.attachment_url`
- `app/main.py`, `request_absence()`

The API accepts and stores any client-supplied string as an attachment URL. It does not upload, validate, scan, authorize, or safely proxy the referenced document.

**Impact:** A professor-facing client could be directed to an untrusted or malicious URL. Private evidence documents also lack a defined access-control and retention model.

**Recommended fix:** Use authenticated object storage with validated uploads, size/type limits, malware scanning as appropriate, opaque object identifiers, short-lived download URLs, and explicit retention rules. Until that exists, clients should not automatically fetch or render arbitrary URLs.

### 10. Notifications cannot be marked as read

**Locations:**

- `app/models.py`, `Notification.read_at`
- `app/main.py`, `notifications()`

The model and response expose `read_at`, but the API provides only a list operation and no authorized endpoint that updates it.

**Impact:** Notification unread badges and read-state synchronization cannot work correctly.

**Recommended fix:** Add an ownership-checked endpoint for marking one notification or all of the current user's notifications as read, with tests preventing users from changing another user's notifications.

## Low-severity problems and maintainability concerns

### 11. The `late` status is never assigned

**Locations:**

- `app/models.py`, `AttendanceStatus`
- `app/main.py`, `record_attendance()`

The model and statistics support `late`, but new attendance records always use the default `present` status. No lateness threshold is implemented.

**Impact:** Late counts always remain zero, making part of the data model and UI misleading.

**Recommended fix:** Define a lateness threshold and assign the status from the server clock, or remove the unused status and related UI until it is supported.

### 12. Deprecated FastAPI startup API

**Location:** `app/main.py`, `@app.on_event("startup")`

The test run reports that `on_event` is deprecated in favor of FastAPI lifespan handlers.

**Impact:** It currently works, but a future FastAPI/Starlette upgrade may remove it.

**Recommended fix:** Move table initialization and demo seeding to an application lifespan context manager.

### 13. Vite configuration uses `__dirname`

**Location:** `frontend/vite.config.ts`

Vite reports that the configuration uses functionality unsupported by its planned native configuration loader.

**Impact:** The frontend builds today, but a future Vite major version may break this configuration.

**Recommended fix:** Replace `path.resolve(__dirname, "./src")` with the supported `import.meta.dirname` equivalent.

### 14. Database schema management is not migration-based

**Locations:**

- `app/main.py`, startup table creation
- `app/db.py`, `ensure_additive_schema()`

The application relies on SQLAlchemy `create_all()` plus a small additive-schema helper instead of a versioned migration framework.

**Impact:** More complex schema changes, rollback, deployment ordering, and environment consistency will become difficult as the application evolves.

**Recommended fix:** Introduce Alembic migrations, establish a baseline migration, and run migrations as an explicit deployment step.

### 15. Test coverage is very small

**Location:** `tests/test_api.py`

Only two backend tests are present. They cover the main attendance flow and enrollment by email, but not most negative, security, statistics, absence, notification, location, concurrency, or frontend behavior.

**Impact:** Authorization and calculation regressions can reach production without being detected.

**Recommended additions:**

- Role-escalation and endpoint-authorization tests.
- Rate-limit and repeated-attempt tests.
- Open/closed/expired/cancelled session statistics.
- Historical enrollment changes.
- QR expiry and code rotation.
- Location boundary and missing-coordinate behavior.
- Concurrent duplicate confirmations.
- Absence-request ownership and review authorization.
- Frontend component or end-to-end tests for login and attendance flows.

### 16. Dependency ranges are broad

**Location:** `pyproject.toml`

The backend image installs from broad version ranges rather than the tracked `uv.lock`. This allowed the container build to install newer package releases and produce deprecation warnings.

**Impact:** Rebuilding the same commit at a later date can produce a different environment or unexpected compatibility failures.

**Recommended fix:** Build production images from the lockfile and update dependencies deliberately through reviewed lockfile changes.

### 17. Some project documentation is stale

**Location:** `docs/presenter-guide.md`, “Known gaps”

The guide claims that several features are absent even though the current implementation includes them, such as student session listing, professor section/student access, attendance record listing, code rotation, logout behavior, and tracked tests.

**Impact:** Reviewers and maintainers may form an incorrect understanding of current behavior.

**Recommended fix:** Update the known-gaps section from the current implementation and keep it synchronized through release reviews.

## Development warnings that are not current failures

- PostgreSQL's Alpine image reports that no system locale package is installed during initial database creation. The database still initializes and becomes healthy.
- `pip` warns about installing packages as root inside the Docker image. This is common during image construction, although running the final application as a non-root user would improve container hardening.
- The test client emits a Starlette compatibility/deprecation warning related to its HTTP client dependency. Tests still pass, but dependency versions should be reviewed together.
- Docker currently publishes the API and frontend on all interfaces. This is convenient for development but should be restricted when network exposure is not intended.

## Suggested remediation order

1. Prevent public professor self-registration.
2. Add production secret validation and disable demo data outside development.
3. Add rate limiting for login and attendance confirmation.
4. Correct open-session and historical-enrollment statistics.
5. Make approved absences produce a defined excused result.
6. Move browser authentication to secure HTTP-only cookies with CSRF protection.
7. Secure absence attachments and add notification read-state support.
8. Expand authorization and statistics test coverage.
9. Add database migrations and locked container dependencies.
10. Resolve deprecations and update stale documentation.
