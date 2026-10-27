from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from hmac import compare_digest
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .attendance_scan import router as attendance_scan_router
from .attendance_scan import students_router
from .config import get_settings
from .db import Base, engine, ensure_additive_schema, get_db
from .models import (
    ATTENDED_STATUSES,
    AttendanceRecord,
    AttendanceStatus,
    AttendanceSession,
    AbsenceRequest,
    AuditLog,
    CodeAttempt,
    CourseSection,
    Enrollment,
    LoginAttempt,
    Notification,
    SessionStatus,
    User,
    as_utc,
)
from .schemas import (
    AttendanceConfirmRequest,
    AttendanceResponse,
    AttendanceSessionCreate,
    AttendanceSessionResponse,
    AbsenceRequestCreate,
    AbsenceRequestResponse,
    AbsenceReview,
    AttendanceOverride,
    AttendanceRecordSummary,
    ClassStatsResponse,
    CodeRotationResponse,
    EnrollmentCreate,
    EnrollmentResponse,
    OpenSessionSummary,
    QrTokenResponse,
    RosterEntry,
    SectionCreate,
    SectionResponse,
    SectionSummary,
    SessionSummary,
    StudentSummary,
    StudentStatsResponse,
    NotificationResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from .security import (
    SESSION_COOKIE,
    code_matches,
    create_token,
    current_user,
    digest_code,
    hash_password,
    make_code,
    make_qr_token,
    require_role,
    verify_password,
)

# Attempts with these results count toward the rate limits.
FAILED_RESULTS = ("invalid", "not_enrolled")

STATIC_DIR = Path(__file__).parent / "static"
DEVELOPMENT_SECRETS = {
    "development-jwt-secret-change-me",
    "development-code-secret-change-me",
    "change-this-in-production",
    "dev-only-jwt-secret-not-for-production-7f3a9c",
    "dev-only-code-secret-not-for-production-b81e4d",
}


def check_production_settings() -> None:
    """Refuse to run a production deployment on guessable signing secrets."""
    settings = get_settings()
    if settings.app_env != "production":
        return
    for name in ("jwt_secret", "code_secret"):
        value = getattr(settings, name)
        if value in DEVELOPMENT_SECRETS or len(value) < 32:
            raise RuntimeError(f"{name.upper()} must be set to a random value of at least 32 characters in production")
    if settings.jwt_secret == settings.code_secret:
        raise RuntimeError("JWT_SECRET and CODE_SECRET must be different")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_production_settings()
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema()
    yield


app = FastAPI(
    title="Attendance API",
    description="Secure code/QR attendance for professors and students.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


def distance_meters(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    earth_radius = 6_371_000
    lat_delta = radians(latitude_b - latitude_a)
    lon_delta = radians(longitude_b - longitude_a)
    value = sin(lat_delta / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(lon_delta / 2) ** 2
    return earth_radius * 2 * asin(sqrt(value))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def ensure_open(session: AttendanceSession) -> None:
    if session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=409, detail="Attendance session is not open")
    if datetime.now(timezone.utc) >= as_utc(session.expires_at):
        raise HTTPException(status_code=410, detail="Attendance session has expired")


def attempt_limit_reached(db: Session, session: AttendanceSession, student_id: str, ip: str, device_id: str | None) -> bool:
    """True when failed attempts for this student, IP, or device exceed the window limit in this session."""
    settings = get_settings()
    window_start = datetime.now(timezone.utc) - timedelta(minutes=settings.attempt_window_minutes)
    scopes = [
        (CodeAttempt.student_id == student_id, settings.attempt_limit_per_student),
        (CodeAttempt.ip_address == ip, settings.attempt_limit_per_ip),
    ]
    if device_id:
        scopes.append((CodeAttempt.device_id == device_id, settings.attempt_limit_per_device))
    for condition, limit in scopes:
        failures = db.scalar(
            select(func.count(CodeAttempt.id)).where(
                CodeAttempt.attendance_session_id == session.id,
                CodeAttempt.attempted_at >= window_start,
                CodeAttempt.result.in_(FAILED_RESULTS),
                condition,
            )
        )
        if failures >= limit:
            return True
    return False


def reject_attempt(
    db: Session,
    session: AttendanceSession,
    student_id: str,
    result: str,
    ip: str,
    device_id: str | None,
    status_code: int,
    detail: str,
) -> NoReturn:
    """Log a rejected confirmation attempt, then return the error to the client."""
    db.add(
        CodeAttempt(
            attendance_session_id=session.id,
            student_id=student_id,
            result=result,
            ip_address=ip,
            device_id=device_id,
        )
    )
    db.commit()
    raise HTTPException(status_code=status_code, detail=detail)


def qr_nonce_for(token: str, session: AttendanceSession) -> str | None:
    """Return the token's one-time nonce if it is a valid, unexpired QR token for the current code version."""
    try:
        claims = jwt.decode(token, get_settings().code_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    if claims.get("session_id") != session.id or claims.get("code_version") != session.code_version:
        return None
    nonce = claims.get("nonce")
    return nonce if isinstance(nonce, str) and nonce else None


def effective_status(session: AttendanceSession) -> str:
    """The stored status, except that an open session past its end time reads as expired."""
    if session.status == SessionStatus.OPEN and datetime.now(timezone.utc) >= as_utc(session.expires_at):
        return "expired"
    return session.status


def owned_section(db: Session, section_id: str, professor: User) -> CourseSection:
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    return section


def owned_session(db: Session, session_id: str, professor: User) -> AttendanceSession:
    session = db.get(AttendanceSession, session_id)
    if session is None or session.opened_by != professor.id:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    return session


def student_summary(user: User) -> StudentSummary:
    return StudentSummary(id=user.id, email=user.email, first_name=user.first_name, last_name=user.last_name)


def absence_response(db: Session, absence: AbsenceRequest) -> AbsenceRequestResponse:
    student = db.get(User, absence.student_id)
    section = db.get(CourseSection, absence.section_id)
    session = db.get(AttendanceSession, absence.attendance_session_id) if absence.attendance_session_id else None
    return AbsenceRequestResponse.model_validate(absence).model_copy(
        update={
            "student_name": f"{student.first_name} {student.last_name}" if student else None,
            "section_name": section.name if section else None,
            "session_date": session.session_date.isoformat() if session else None,
            "has_attachment": absence.attachment_path is not None,
        }
    )


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=201, tags=["auth"])
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    if payload.role == "professor":
        expected = get_settings().professor_signup_code
        if not expected or not compare_digest((payload.professor_code or "").encode(), expected.encode()):
            raise HTTPException(status_code=403, detail="Professor accounts need the access code from your department")
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Check credentials, refusing further tries once an email has too many recent failures."""
    settings = get_settings()
    email = email.lower()
    window_start = datetime.now(timezone.utc) - timedelta(minutes=settings.login_window_minutes)
    failures = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at >= window_start,
        )
    )
    if failures >= settings.login_attempt_limit:
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again later.")
    user = db.scalar(select(User).where(User.email == email))
    ok = user is not None and user.is_active and verify_password(password, user.password_hash)
    db.add(LoginAttempt(email=email, succeeded=ok))
    db.commit()
    if not ok:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return user


@app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["auth"])
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenResponse:
    return TokenResponse(access_token=create_token(authenticate(db, form.username, form.password)))


@app.post("/api/v1/auth/login", response_model=UserResponse, tags=["auth"])
def browser_login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> User:
    """Sign in from the web UI: the token is stored in an HTTP-only cookie rather than returned."""
    user = authenticate(db, form.username, form.password)
    response.set_cookie(
        SESSION_COOKIE,
        create_token(user),
        max_age=get_settings().access_token_minutes * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
    )
    return user


@app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def browser_logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict")


@app.get("/api/v1/users/me", response_model=UserResponse, tags=["auth"])
def me(user: User = Depends(current_user)) -> User:
    return user


@app.post("/api/v1/sections", response_model=SectionResponse, status_code=201, tags=["professor"])
def create_section(
    payload: SectionCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> CourseSection:
    section = CourseSection(name=payload.name, professor_id=professor.id)
    db.add(section)
    db.commit()
    db.refresh(section)
    return section


@app.post(
    "/api/v1/sections/{section_id}/enrollments",
    response_model=EnrollmentResponse,
    status_code=201,
    tags=["professor"],
)
def enroll_student(
    section_id: str,
    payload: EnrollmentCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> EnrollmentResponse:
    owned_section(db, section_id, professor)
    if payload.student_id:
        student = db.get(User, payload.student_id)
    else:
        student = db.scalar(select(User).where(User.email == payload.email.lower()))
    if student is None or student.role != "student":
        raise HTTPException(status_code=404, detail="No student account with that email")
    existing = db.scalar(
        select(Enrollment).where(Enrollment.section_id == section_id, Enrollment.student_id == student.id)
    )
    if existing is not None and existing.status != "active":
        # Re-adding a student who was removed earlier.
        existing.status = "active"
        existing.ended_at = None
        db.commit()
        return EnrollmentResponse(status="enrolled", student=student_summary(student))
    enrollment = Enrollment(section_id=section_id, student_id=student.id)
    db.add(enrollment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Student is already enrolled") from exc
    return EnrollmentResponse(status="enrolled", student=student_summary(student))


@app.delete("/api/v1/sections/{section_id}/enrollments/{student_id}", status_code=204, tags=["professor"])
def remove_student(
    section_id: str,
    student_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> None:
    """Take a student out of a class. Their past attendance is kept; later sessions no longer count for them."""
    owned_section(db, section_id, professor)
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.section_id == section_id,
            Enrollment.student_id == student_id,
            Enrollment.status == "active",
        )
    )
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Student is not in this class")
    enrollment.status = "removed"
    enrollment.ended_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=professor.id, action="student_removed", entity_type="enrollment", entity_id=enrollment.id))
    db.commit()


@app.post(
    "/api/v1/attendance-sessions",
    response_model=AttendanceSessionResponse,
    status_code=201,
    tags=["professor"],
)
def open_attendance(
    payload: AttendanceSessionCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> AttendanceSessionResponse:
    section = db.get(CourseSection, payload.section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    location_values = (payload.location_latitude, payload.location_longitude, payload.allowed_radius_meters)
    if any(value is not None for value in location_values) and not all(value is not None for value in location_values):
        raise HTTPException(status_code=422, detail="Provide latitude, longitude, and radius together")
    code = make_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=payload.duration_minutes)
    session = AttendanceSession(
        section_id=section.id,
        opened_by=professor.id,
        session_date=datetime.now(timezone.utc).date(),
        expires_at=expires_at,
        code_hash=digest_code(code),
        location_latitude=payload.location_latitude,
        location_longitude=payload.location_longitude,
        allowed_radius_meters=payload.allowed_radius_meters,
        late_after_minutes=payload.late_after_minutes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    qr_token, _ = make_qr_token(session.id, session.code_version, expires_at)
    return AttendanceSessionResponse(
        id=session.id,
        section_id=session.section_id,
        expires_at=expires_at,
        code=code,
        qr_token=qr_token,
        location_required=session.location_latitude is not None,
    )


@app.post(
    "/api/v1/attendance-sessions/{session_id}/qr-token",
    response_model=QrTokenResponse,
    tags=["professor"],
)
def refresh_qr_token(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> QrTokenResponse:
    """Issue a fresh single-use QR token. Call again to refresh the QR shown on screen."""
    session = db.get(AttendanceSession, session_id)
    if session is None or session.opened_by != professor.id:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    ensure_open(session)
    qr_token, expires_at = make_qr_token(session.id, session.code_version, as_utc(session.expires_at))
    return QrTokenResponse(qr_token=qr_token, expires_at=expires_at)


@app.post(
    "/api/v1/attendance-sessions/{session_id}/rotate-code",
    response_model=CodeRotationResponse,
    tags=["professor"],
)
def rotate_code(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> CodeRotationResponse:
    """Replace the session code. The old code and any QR tokens issued before this call stop working."""
    session = db.get(AttendanceSession, session_id)
    if session is None or session.opened_by != professor.id:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    ensure_open(session)
    code = make_code()
    session.code_hash = digest_code(code)
    session.code_version += 1
    qr_token, expires_at = make_qr_token(session.id, session.code_version, as_utc(session.expires_at))
    db.add(
        AuditLog(actor_id=professor.id, action="attendance_code_rotated", entity_type="attendance_session", entity_id=session.id)
    )
    db.commit()
    db.refresh(session)
    return CodeRotationResponse(
        code=code,
        code_version=session.code_version,
        qr_token=qr_token,
        expires_at=expires_at,
    )


@app.post("/api/v1/attendance-sessions/{session_id}/confirm", response_model=AttendanceResponse, tags=["student"])
def confirm_attendance(
    session_id: str,
    payload: AttendanceConfirmRequest,
    request: Request,
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
    device_id: str | None = Header(default=None, alias="X-Device-Id", max_length=128),
) -> AttendanceResponse:
    if not payload.code and not payload.qr_token:
        raise HTTPException(status_code=422, detail="Provide code or qr_token")
    session = db.get(AttendanceSession, session_id)
    if session is None or session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=404, detail="Attendance session is not open")
    if datetime.now(timezone.utc) >= as_utc(session.expires_at):
        raise HTTPException(status_code=410, detail="Attendance session has expired")
    ip = client_ip(request)
    if attempt_limit_reached(db, session, student.id, ip, device_id):
        reject_attempt(
            db, session, student.id, "rate_limited", ip, device_id,
            429, "Too many failed attempts. Try again later.",
        )
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.section_id == session.section_id,
            Enrollment.student_id == student.id,
            Enrollment.status == "active",
        )
    )
    if enrollment is None:
        reject_attempt(db, session, student.id, "not_enrolled", ip, device_id, 403, "Student is not enrolled")
    code_ok = bool(payload.code and code_matches(payload.code, session.code_hash))
    qr_nonce = qr_nonce_for(payload.qr_token, session) if payload.qr_token else None
    if not code_ok and qr_nonce is None:
        reject_attempt(db, session, student.id, "invalid", ip, device_id, 400, "Invalid attendance code or QR")
    # A valid QR is single-use: its nonce may appear on one record only.
    method = "qr" if qr_nonce else "code"
    if qr_nonce and db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.qr_nonce == qr_nonce)):
        reject_attempt(db, session, student.id, "invalid", ip, device_id, 409, "QR code has already been used")
    location_required = session.location_latitude is not None
    if location_required and (payload.latitude is None or payload.longitude is None):
        raise HTTPException(status_code=422, detail="Location is required for this attendance session")
    if location_required and distance_meters(
        session.location_latitude,
        session.location_longitude,
        payload.latitude,
        payload.longitude,
    ) > session.allowed_radius_meters:
        raise HTTPException(status_code=403, detail="You are outside the attendance location")
    # One device may confirm attendance for only one student per session.
    if device_id and db.scalar(
        select(AttendanceRecord.id).where(
            AttendanceRecord.attendance_session_id == session.id,
            AttendanceRecord.device_id == device_id,
            AttendanceRecord.student_id != student.id,
        )
    ):
        reject_attempt(
            db, session, student.id, "device_conflict", ip, device_id,
            409, "This device already confirmed attendance for another student",
        )
    now = datetime.now(timezone.utc)
    late = session.late_after_minutes is not None and now >= as_utc(session.starts_at) + timedelta(
        minutes=session.late_after_minutes
    )
    record = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.attendance_session_id == session.id,
            AttendanceRecord.student_id == student.id,
        )
    )
    if record is not None and record.status not in ATTENDED_STATUSES:
        # An excused student who turns up after all is recorded as attending.
        db.delete(record)
        db.flush()
    elif record is not None:
        raise HTTPException(status_code=409, detail="Attendance already confirmed")
    record = AttendanceRecord(
        attendance_session_id=session.id,
        student_id=student.id,
        status=AttendanceStatus.LATE if late else AttendanceStatus.PRESENT,
        confirmation_method=method,
        confirmed_at=now,
        latitude=payload.latitude,
        longitude=payload.longitude,
        qr_nonce=qr_nonce,
        ip_address=ip,
        device_id=device_id,
    )
    db.add(record)
    db.add(
        CodeAttempt(
            attendance_session_id=session.id,
            student_id=student.id,
            result="accepted",
            ip_address=ip,
            device_id=device_id,
        )
    )
    db.add(AuditLog(actor_id=student.id, action="attendance_confirmed", entity_type="attendance_session", entity_id=session.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Attendance already confirmed") from exc
    db.refresh(record)
    return AttendanceResponse(
        id=record.id,
        session_id=record.attendance_session_id,
        status=record.status,
        confirmation_method=record.confirmation_method,
        confirmed_at=record.confirmed_at,
    )


@app.post("/api/v1/attendance-sessions/{session_id}/cancel", tags=["professor"])
def cancel_attendance(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = db.get(AttendanceSession, session_id)
    if session is None or session.opened_by != professor.id:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    if session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=409, detail="Attendance session is already closed")
    session.status = SessionStatus.CANCELLED
    student_ids = db.scalars(
        select(Enrollment.student_id).where(Enrollment.section_id == session.section_id, Enrollment.status == "active")
    ).all()
    for student_id in student_ids:
        db.add(
            Notification(
                user_id=student_id,
                title="Class attendance cancelled",
                message=f"Attendance for {session.session_date.isoformat()} was cancelled by your professor.",
            )
        )
    db.add(AuditLog(actor_id=professor.id, action="attendance_cancelled", entity_type="attendance_session", entity_id=session.id))
    db.commit()
    return {"status": "cancelled", "notifications_sent": str(len(student_ids))}


@app.post("/api/v1/absence-requests", response_model=AbsenceRequestResponse, status_code=201, tags=["absence"])
def request_absence(
    payload: AbsenceRequestCreate,
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> AbsenceRequestResponse:
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.section_id == payload.section_id,
            Enrollment.student_id == student.id,
            Enrollment.status == "active",
        )
    )
    if enrollment is None:
        raise HTTPException(status_code=403, detail="Student is not enrolled")
    if payload.attendance_session_id:
        session = db.get(AttendanceSession, payload.attendance_session_id)
        if session is None or session.section_id != payload.section_id:
            raise HTTPException(status_code=404, detail="Attendance session not found")
    absence = AbsenceRequest(
        student_id=student.id,
        section_id=payload.section_id,
        attendance_session_id=payload.attendance_session_id,
        reason=payload.reason,
        attachment_url=payload.attachment_url,
    )
    db.add(absence)
    db.commit()
    db.refresh(absence)
    return absence_response(db, absence)


@app.get(
    "/api/v1/sections/{section_id}/absence-requests",
    response_model=list[AbsenceRequestResponse],
    tags=["absence"],
)
def section_absence_requests(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[AbsenceRequestResponse]:
    owned_section(db, section_id, professor)
    requests = db.scalars(
        select(AbsenceRequest)
        .where(AbsenceRequest.section_id == section_id)
        .order_by(AbsenceRequest.created_at.desc())
    ).all()
    return [absence_response(db, absence) for absence in requests]


@app.patch("/api/v1/absence-requests/{request_id}", response_model=AbsenceRequestResponse, tags=["absence"])
def review_absence(
    request_id: str,
    payload: AbsenceReview,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> AbsenceRequestResponse:
    absence = db.get(AbsenceRequest, request_id)
    section = db.get(CourseSection, absence.section_id) if absence else None
    if absence is None or section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Absence request not found")
    if absence.status != "pending":
        raise HTTPException(status_code=409, detail="Absence request was already reviewed")
    absence.status = payload.status
    excused_note = ""
    if payload.status == "approved" and absence.attendance_session_id:
        # Approval excuses the student from that session, unless they were in class anyway.
        existing = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.attendance_session_id == absence.attendance_session_id,
                AttendanceRecord.student_id == absence.student_id,
            )
        )
        if existing is None:
            db.add(
                AttendanceRecord(
                    attendance_session_id=absence.attendance_session_id,
                    student_id=absence.student_id,
                    status=AttendanceStatus.EXCUSED,
                    confirmation_method="absence_request",
                )
            )
            excused_note = " That class no longer counts against your attendance."
    db.add(
        Notification(
            user_id=absence.student_id,
            title="Absence request updated",
            message=f"Your absence request for {section.name} was {payload.status}.{excused_note}",
        )
    )
    db.add(AuditLog(actor_id=professor.id, action=f"absence_{payload.status}", entity_type="absence_request", entity_id=absence.id))
    db.commit()
    db.refresh(absence)
    return absence_response(db, absence)


# File signatures we accept as evidence, mapped to the content type we serve them with.
ATTACHMENT_SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def upload_dir() -> Path:
    path = Path(get_settings().upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@app.post("/api/v1/absence-requests/{request_id}/attachment", response_model=AbsenceRequestResponse, tags=["absence"])
async def upload_absence_attachment(
    request_id: str,
    file: UploadFile = File(...),
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> AbsenceRequestResponse:
    """Attach a PDF, PNG, or JPEG (for example a doctor's note) to your own pending request."""
    absence = db.get(AbsenceRequest, request_id)
    if absence is None or absence.student_id != student.id:
        raise HTTPException(status_code=404, detail="Absence request not found")
    if absence.status != "pending":
        raise HTTPException(status_code=409, detail="Absence request was already reviewed")
    limit = get_settings().max_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File is too large. The limit is {limit // (1024 * 1024)} MB.")
    # Trust the file's bytes, not its name or the browser's claimed type.
    content_type = next((kind for magic, kind in ATTACHMENT_SIGNATURES.items() if content.startswith(magic)), None)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Attach a PDF, PNG, or JPEG file")
    stored_name = uuid4().hex
    (upload_dir() / stored_name).write_bytes(content)
    if absence.attachment_path:
        (upload_dir() / absence.attachment_path).unlink(missing_ok=True)
    absence.attachment_path = stored_name
    absence.attachment_name = Path(file.filename or "attachment").name[:255]
    absence.attachment_type = content_type
    db.commit()
    db.refresh(absence)
    return absence_response(db, absence)


@app.get("/api/v1/absence-requests/{request_id}/attachment", tags=["absence"])
def download_absence_attachment(
    request_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Only the student who sent the request and the class's professor can open the file."""
    absence = db.get(AbsenceRequest, request_id)
    section = db.get(CourseSection, absence.section_id) if absence else None
    allowed = absence is not None and (
        absence.student_id == user.id or (section is not None and section.professor_id == user.id)
    )
    if not allowed or not absence.attachment_path:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = upload_dir() / absence.attachment_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(
        path,
        media_type=absence.attachment_type,
        filename=absence.attachment_name,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@app.get("/api/v1/notifications", response_model=list[NotificationResponse], tags=["notifications"])
def notifications(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Notification]:
    return db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100)
    ).all()


def enrolled_at_start(enrollment: Enrollment | None, session: AttendanceSession) -> bool:
    """Whether the student was in the class when the session started."""
    if enrollment is None:
        return False
    started = as_utc(session.starts_at)
    if enrollment.enrolled_at is not None and as_utc(enrollment.enrolled_at) > started:
        return False
    return enrollment.ended_at is None or as_utc(enrollment.ended_at) > started


def attendance_rate(attended: int, counted: int, excused: int) -> float:
    """Share of non-excused sessions attended, as a percentage."""
    expected = counted - excused
    return round(attended * 100 / expected, 2) if expected > 0 else 0


@app.get("/api/v1/students/me/statistics", response_model=StudentStatsResponse, tags=["statistics"])
def student_statistics(
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> StudentStatsResponse:
    enrollments = {
        enrollment.section_id: enrollment
        for enrollment in db.scalars(select(Enrollment).where(Enrollment.student_id == student.id)).all()
    }
    records = {
        record.attendance_session_id: record
        for record in db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student.id)).all()
    }
    # Sessions of the student's classes, plus labs of other groups they were recorded at.
    sessions = db.scalars(
        select(AttendanceSession)
        .where(
            or_(
                AttendanceSession.section_id.in_(enrollments.keys()),
                AttendanceSession.id.in_(records.keys()),
            ),
            AttendanceSession.status != SessionStatus.CANCELLED,
        )
        .order_by(AttendanceSession.starts_at)
    ).all()
    # A session counts once the student has a record for it, or once it has ended and they were enrolled at its start.
    sessions = [
        session
        for session in sessions
        if session.id in records
        or (effective_status(session) != SessionStatus.OPEN and enrolled_at_start(enrollments[session.section_id], session))
    ]
    section_names = {
        section.id: section.name
        for section in db.scalars(
            select(CourseSection).where(CourseSection.id.in_({session.section_id for session in sessions}))
        ).all()
    }
    by_session = [
        {
            "session_id": session.id,
            "date": session.session_date.isoformat(),
            "starts_at": as_utc(session.starts_at).isoformat(),
            "section_id": session.section_id,
            "section_name": section_names.get(session.section_id),
            "status": records[session.id].status if session.id in records else "absent",
        }
        for session in sessions
    ]
    counts = {status: sum(1 for item in by_session if item["status"] == status) for status in ("present", "late", "excused", "absent")}
    attended = counts["present"] + counts["late"]
    return StudentStatsResponse(
        student_id=student.id,
        total_sessions=len(by_session),
        attended_sessions=attended,
        attendance_percentage=attendance_rate(attended, len(by_session), counts["excused"]),
        present=counts["present"],
        late=counts["late"],
        excused=counts["excused"],
        absent=counts["absent"],
        by_session=by_session,
    )


@app.get("/api/v1/sections/{section_id}/statistics", response_model=ClassStatsResponse, tags=["statistics"])
def class_statistics(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> ClassStatsResponse:
    """Statistics over finished sessions; a session still taking attendance is left out until it ends."""
    owned_section(db, section_id, professor)
    sessions = [
        session
        for session in db.scalars(
            select(AttendanceSession)
            .where(AttendanceSession.section_id == section_id, AttendanceSession.status != SessionStatus.CANCELLED)
            .order_by(AttendanceSession.starts_at)
        ).all()
        if effective_status(session) != SessionStatus.OPEN
    ]
    enrollments = db.scalars(select(Enrollment).where(Enrollment.section_id == section_id)).all()
    records_by_session: dict[str, list[AttendanceRecord]] = {}
    for record in db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.attendance_session_id.in_([session.id for session in sessions]))
    ).all():
        records_by_session.setdefault(record.attendance_session_id, []).append(record)
    by_session = []
    total_attended = total_expected = 0
    for session in sessions:
        records = records_by_session.get(session.id, [])
        expected = {enrollment.student_id for enrollment in enrollments if enrolled_at_start(enrollment, session)}
        expected |= {record.student_id for record in records}
        attended = sum(1 for record in records if record.status in ATTENDED_STATUSES)
        excused = sum(1 for record in records if record.status == AttendanceStatus.EXCUSED)
        total_attended += attended
        total_expected += len(expected) - excused
        by_session.append(
            {
                "session_id": session.id,
                "date": session.session_date.isoformat(),
                "starts_at": as_utc(session.starts_at).isoformat(),
                "confirmed": attended,
                "excused": excused,
                "enrolled": len(expected),
                "attendance_percentage": attendance_rate(attended, len(expected), excused),
            }
        )
    return ClassStatsResponse(
        section_id=section_id,
        total_sessions=len(sessions),
        enrolled_students=sum(1 for enrollment in enrollments if enrollment.status == "active"),
        total_confirmations=total_attended,
        attendance_percentage=round(total_attended * 100 / total_expected, 2) if total_expected else 0,
        by_session=by_session,
    )


@app.get("/api/v1/sections", response_model=list[SectionSummary], tags=["sections"])
def list_sections(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[SectionSummary]:
    """Professors see the sections they teach; students see the sections they are enrolled in."""
    if user.role == "professor":
        query = select(CourseSection).where(CourseSection.professor_id == user.id)
    else:
        query = (
            select(CourseSection)
            .join(Enrollment, Enrollment.section_id == CourseSection.id)
            .where(Enrollment.student_id == user.id, Enrollment.status == "active")
        )
    summaries = []
    for section in db.scalars(query.order_by(CourseSection.name)).all():
        student_count = db.scalar(
            select(func.count(Enrollment.id)).where(Enrollment.section_id == section.id, Enrollment.status == "active")
        )
        open_session = db.scalar(
            select(AttendanceSession)
            .where(
                AttendanceSession.section_id == section.id,
                AttendanceSession.status == SessionStatus.OPEN,
                AttendanceSession.expires_at > datetime.now(timezone.utc),
            )
            .order_by(AttendanceSession.starts_at.desc())
        )
        open_summary = None
        if open_session is not None:
            confirmed = user.role == "student" and db.scalar(
                select(AttendanceRecord.id).where(
                    AttendanceRecord.attendance_session_id == open_session.id,
                    AttendanceRecord.student_id == user.id,
                    AttendanceRecord.status.in_(ATTENDED_STATUSES),
                )
            ) is not None
            open_summary = OpenSessionSummary(
                id=open_session.id,
                expires_at=open_session.expires_at,
                location_required=open_session.location_latitude is not None,
                already_confirmed=confirmed,
            )
        summaries.append(
            SectionSummary(
                id=section.id,
                name=section.name,
                professor_id=section.professor_id,
                professor_name=f"{section.professor.first_name} {section.professor.last_name}",
                student_count=student_count,
                open_session=open_summary,
            )
        )
    return summaries


@app.get("/api/v1/sections/{section_id}/students", response_model=list[StudentSummary], tags=["professor"])
def section_students(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[StudentSummary]:
    owned_section(db, section_id, professor)
    students = db.scalars(
        select(User)
        .join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.section_id == section_id, Enrollment.status == "active")
        .order_by(User.last_name, User.first_name)
    ).all()
    return [student_summary(student) for student in students]


@app.get("/api/v1/sections/{section_id}/sessions", response_model=list[SessionSummary], tags=["professor"])
def section_sessions(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[SessionSummary]:
    owned_section(db, section_id, professor)
    sessions = db.scalars(
        select(AttendanceSession)
        .where(AttendanceSession.section_id == section_id)
        .order_by(AttendanceSession.starts_at.desc())
    ).all()
    counts = dict(
        db.execute(
            select(AttendanceRecord.attendance_session_id, func.count(AttendanceRecord.id))
            .join(AttendanceSession)
            .where(AttendanceSession.section_id == section_id, AttendanceRecord.status.in_(ATTENDED_STATUSES))
            .group_by(AttendanceRecord.attendance_session_id)
        ).all()
    )
    enrollments = db.scalars(select(Enrollment).where(Enrollment.section_id == section_id)).all()
    return [
        SessionSummary(
            id=session.id,
            section_id=session.section_id,
            session_date=session.session_date.isoformat(),
            starts_at=session.starts_at,
            expires_at=session.expires_at,
            status=effective_status(session),
            confirmed=counts.get(session.id, 0),
            eligible=sum(1 for enrollment in enrollments if enrolled_at_start(enrollment, session)),
            location_required=session.location_latitude is not None,
            late_after_minutes=session.late_after_minutes,
        )
        for session in sessions
    ]


@app.get(
    "/api/v1/attendance-sessions/{session_id}/records",
    response_model=list[AttendanceRecordSummary],
    tags=["professor"],
)
def session_records(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[AttendanceRecordSummary]:
    """Live list of students in class (present or late) for the professor's screen, newest first."""
    owned_session(db, session_id, professor)
    rows = db.execute(
        select(AttendanceRecord, User)
        .join(User, User.id == AttendanceRecord.student_id)
        .where(AttendanceRecord.attendance_session_id == session_id, AttendanceRecord.status.in_(ATTENDED_STATUSES))
        .order_by(AttendanceRecord.confirmed_at.desc())
    ).all()
    return [
        AttendanceRecordSummary(
            student=student_summary(student),
            status=record.status,
            confirmation_method=record.confirmation_method,
            confirmed_at=record.confirmed_at,
        )
        for record, student in rows
    ]


@app.get("/api/v1/attendance-sessions/{session_id}/roster", response_model=list[RosterEntry], tags=["professor"])
def session_roster(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[RosterEntry]:
    """Every student this session counts for, with their status, so the professor can correct it."""
    session = owned_session(db, session_id, professor)
    records = {
        record.student_id: record
        for record in db.scalars(select(AttendanceRecord).where(AttendanceRecord.attendance_session_id == session_id)).all()
    }
    student_ids = set(records)
    for enrollment in db.scalars(select(Enrollment).where(Enrollment.section_id == session.section_id)).all():
        if enrolled_at_start(enrollment, session) or enrollment.status == "active" and effective_status(session) == SessionStatus.OPEN:
            student_ids.add(enrollment.student_id)
    students = db.scalars(select(User).where(User.id.in_(student_ids)).order_by(User.last_name, User.first_name)).all()
    missing = "pending" if effective_status(session) == SessionStatus.OPEN else "absent"
    return [
        RosterEntry(
            student=student_summary(student),
            status=records[student.id].status if student.id in records else missing,
            confirmation_method=records[student.id].confirmation_method if student.id in records else None,
            confirmed_at=records[student.id].confirmed_at if student.id in records else None,
        )
        for student in students
    ]


@app.put(
    "/api/v1/attendance-sessions/{session_id}/students/{student_id}",
    response_model=RosterEntry,
    tags=["professor"],
)
def set_attendance(
    session_id: str,
    student_id: str,
    payload: AttendanceOverride,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> RosterEntry:
    """Correct one student's attendance by hand, for example when their phone was dead."""
    session = owned_session(db, session_id, professor)
    if session.status == SessionStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="This class was cancelled")
    student = db.get(User, student_id)
    enrollment = db.scalar(
        select(Enrollment).where(Enrollment.section_id == session.section_id, Enrollment.student_id == student_id)
    )
    if student is None or enrollment is None:
        raise HTTPException(status_code=404, detail="Student is not in this class")
    record = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.attendance_session_id == session_id,
            AttendanceRecord.student_id == student_id,
        )
    )
    if payload.status == "absent":
        if record is not None:
            db.delete(record)
        record = None
    elif record is None:
        record = AttendanceRecord(
            attendance_session_id=session_id,
            student_id=student_id,
            status=payload.status,
            confirmation_method="manual",
        )
        db.add(record)
    else:
        record.status = payload.status
        record.confirmation_method = "manual"
    db.add(
        Notification(
            user_id=student_id,
            title="Attendance updated",
            message=f"Your professor marked you {payload.status} for the class on {session.session_date.isoformat()}.",
        )
    )
    db.add(AuditLog(actor_id=professor.id, action=f"attendance_set_{payload.status}", entity_type="attendance_session", entity_id=session_id))
    db.commit()
    if record is not None:
        db.refresh(record)
    if record is None:
        status = "pending" if effective_status(session) == SessionStatus.OPEN else "absent"
    else:
        status = record.status
    return RosterEntry(
        student=student_summary(student),
        status=status,
        confirmation_method=record.confirmation_method if record else None,
        confirmed_at=record.confirmed_at if record else None,
    )


@app.post("/api/v1/attendance-sessions/{session_id}/close", tags=["professor"])
def close_attendance(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """End attendance early. Unlike cancelling, the session still counts in statistics."""
    session = owned_session(db, session_id, professor)
    if session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=409, detail="Attendance session is already closed")
    session.status = SessionStatus.CLOSED
    if as_utc(session.expires_at) > datetime.now(timezone.utc):
        session.expires_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=professor.id, action="attendance_closed", entity_type="attendance_session", entity_id=session.id))
    db.commit()
    return {"status": "closed"}


@app.get("/api/v1/absence-requests/me", response_model=list[AbsenceRequestResponse], tags=["absence"])
def my_absence_requests(
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> list[AbsenceRequestResponse]:
    requests = db.scalars(
        select(AbsenceRequest)
        .where(AbsenceRequest.student_id == student.id)
        .order_by(AbsenceRequest.created_at.desc())
    ).all()
    return [absence_response(db, absence) for absence in requests]


@app.post("/api/v1/notifications/read", status_code=204, tags=["notifications"])
def mark_notifications_read(user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    for notification in db.scalars(
        select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    ).all():
        notification.read_at = datetime.now(timezone.utc)
    db.commit()


app.include_router(attendance_scan_router)
app.include_router(students_router)

# Registered last so every API route above takes precedence over the web UI's files.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
