from datetime import date, datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Base, engine, ensure_additive_schema, get_db
from .models import (
    AttendanceRecord,
    AttendanceSession,
    AbsenceRequest,
    AuditLog,
    CodeAttempt,
    CourseSection,
    Enrollment,
    Notification,
    SessionStatus,
    User,
)
from .schemas import (
    AttendanceCodeConfirmRequest,
    AttendanceConfirmRequest,
    AttendanceRecordRow,
    AttendanceResponse,
    AttendanceSessionCreate,
    AttendanceSessionResponse,
    AttendanceSessionSummary,
    AbsenceRequestCreate,
    AbsenceRequestResponse,
    AbsenceReview,
    ClassStatsResponse,
    EnrollmentCreate,
    SectionCreate,
    SectionResponse,
    SectionSummary,
    StudentStatsResponse,
    NotificationResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from .seed import seed_demo_data
from .security import (
    code_matches,
    create_token,
    current_user,
    digest_code,
    hash_password,
    make_code,
    require_role,
    verify_password,
)

app = FastAPI(
    title="Attendance API",
    description="Secure code/QR attendance for professors and students.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive values as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def distance_meters(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    earth_radius = 6_371_000
    lat_delta = radians(latitude_b - latitude_a)
    lon_delta = radians(longitude_b - longitude_a)
    value = sin(lat_delta / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(lon_delta / 2) ** 2
    return earth_radius * 2 * asin(sqrt(value))


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema()
    if get_settings().seed_demo_data:
        seed_demo_data()


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=201, tags=["auth"])
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
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


@app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["auth"])
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return TokenResponse(access_token=create_token(user))


@app.get("/api/v1/auth/me", response_model=UserResponse, tags=["auth"])
def me(user: User = Depends(current_user)) -> User:
    return user


@app.get("/api/v1/sections", response_model=list[SectionSummary], tags=["sections"])
def list_sections(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[SectionSummary]:
    if user.role == "professor":
        sections = db.scalars(
            select(CourseSection).where(CourseSection.professor_id == user.id).order_by(CourseSection.name)
        ).all()
    else:
        sections = db.scalars(
            select(CourseSection)
            .join(Enrollment, Enrollment.section_id == CourseSection.id)
            .where(Enrollment.student_id == user.id, Enrollment.status == "active")
            .order_by(CourseSection.name)
        ).all()
    result = []
    for section in sections:
        enrolled = db.scalar(
            select(func.count()).select_from(Enrollment).where(
                Enrollment.section_id == section.id, Enrollment.status == "active"
            )
        )
        total_sessions = db.scalar(
            select(func.count()).select_from(AttendanceSession).where(
                AttendanceSession.section_id == section.id, AttendanceSession.status != SessionStatus.CANCELLED
            )
        )
        attended = None
        if user.role == "student":
            attended = db.scalar(
                select(func.count())
                .select_from(AttendanceRecord)
                .join(AttendanceSession)
                .where(AttendanceSession.section_id == section.id, AttendanceRecord.student_id == user.id)
            )
        result.append(
            SectionSummary(
                id=section.id,
                name=section.name,
                professor_id=section.professor_id,
                professor_name=f"{section.professor.first_name} {section.professor.last_name}",
                enrolled_students=enrolled or 0,
                total_sessions=total_sessions or 0,
                attended_sessions=attended,
            )
        )
    return result


@app.get("/api/v1/sections/{section_id}/students", response_model=list[UserResponse], tags=["professor"])
def section_students(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[User]:
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    return db.scalars(
        select(User)
        .join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.section_id == section_id, Enrollment.status == "active")
        .order_by(User.last_name, User.first_name)
    ).all()


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


@app.post("/api/v1/sections/{section_id}/enrollments", status_code=201, tags=["professor"])
def enroll_student(
    section_id: str,
    payload: EnrollmentCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    section = db.get(CourseSection, section_id)
    if payload.student_id:
        student = db.get(User, payload.student_id)
    elif payload.student_email:
        student = db.scalar(select(User).where(User.email == payload.student_email.lower()))
    else:
        raise HTTPException(status_code=422, detail="Provide student_id or student_email")
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    if student is None or student.role != "student":
        raise HTTPException(status_code=404, detail="Student not found")
    enrollment = Enrollment(section_id=section_id, student_id=student.id)
    db.add(enrollment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Student is already enrolled") from exc
    return {"status": "enrolled"}


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
        title=payload.title,
        session_date=date.today(),
        expires_at=expires_at,
        code_hash=digest_code(code),
        location_latitude=payload.location_latitude,
        location_longitude=payload.location_longitude,
        allowed_radius_meters=payload.allowed_radius_meters,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session_credentials(session, code)


def session_credentials(session: AttendanceSession, code: str) -> AttendanceSessionResponse:
    expires_at = as_utc(session.expires_at)
    qr_token = jwt.encode(
        {"session_id": session.id, "code_version": session.code_version, "exp": expires_at},
        get_settings().code_secret,
        algorithm="HS256",
    )
    return AttendanceSessionResponse(
        id=session.id,
        section_id=session.section_id,
        title=session.title,
        expires_at=expires_at,
        code=code,
        qr_token=qr_token,
        location_required=session.location_latitude is not None,
    )


def session_summary(db: Session, session: AttendanceSession) -> AttendanceSessionSummary:
    section = db.get(CourseSection, session.section_id)
    confirmed = db.scalar(
        select(func.count()).select_from(AttendanceRecord).where(AttendanceRecord.attendance_session_id == session.id)
    )
    enrolled = db.scalar(
        select(func.count()).select_from(Enrollment).where(
            Enrollment.section_id == session.section_id, Enrollment.status == "active"
        )
    )
    expires_at = as_utc(session.expires_at)
    status_value = session.status
    if status_value == SessionStatus.OPEN and datetime.now(timezone.utc) >= expires_at:
        status_value = SessionStatus.CLOSED
    return AttendanceSessionSummary(
        id=session.id,
        section_id=session.section_id,
        section_name=section.name if section else "",
        title=session.title,
        status=status_value,
        session_date=session.session_date,
        starts_at=as_utc(session.starts_at),
        expires_at=expires_at,
        location_required=session.location_latitude is not None,
        confirmed_count=confirmed or 0,
        enrolled_count=enrolled or 0,
    )


@app.get("/api/v1/attendance-sessions", response_model=list[AttendanceSessionSummary], tags=["sessions"])
def list_attendance_sessions(
    section_id: str | None = None,
    open_only: bool = Query(default=False, alias="open"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AttendanceSessionSummary]:
    query = select(AttendanceSession).join(CourseSection, CourseSection.id == AttendanceSession.section_id)
    if user.role == "professor":
        query = query.where(CourseSection.professor_id == user.id)
    else:
        query = query.join(Enrollment, Enrollment.section_id == CourseSection.id).where(
            Enrollment.student_id == user.id, Enrollment.status == "active"
        )
    if section_id:
        query = query.where(AttendanceSession.section_id == section_id)
    if open_only:
        query = query.where(
            AttendanceSession.status == SessionStatus.OPEN,
            AttendanceSession.expires_at > datetime.now(timezone.utc),
        )
    sessions = db.scalars(query.order_by(AttendanceSession.starts_at.desc()).limit(200)).all()
    return [session_summary(db, session) for session in sessions]


def owned_session(db: Session, session_id: str, professor: User) -> AttendanceSession:
    session = db.get(AttendanceSession, session_id)
    if session is None or session.opened_by != professor.id:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    return session


@app.get(
    "/api/v1/attendance-sessions/{session_id}/records",
    response_model=list[AttendanceRecordRow],
    tags=["professor"],
)
def session_records(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[AttendanceRecordRow]:
    session = owned_session(db, session_id, professor)
    rows = db.execute(
        select(AttendanceRecord, User)
        .join(User, User.id == AttendanceRecord.student_id)
        .where(AttendanceRecord.attendance_session_id == session.id)
        .order_by(AttendanceRecord.confirmed_at)
    ).all()
    return [
        AttendanceRecordRow(
            id=record.id,
            student_id=student.id,
            first_name=student.first_name,
            last_name=student.last_name,
            email=student.email,
            status=record.status,
            confirmation_method=record.confirmation_method,
            confirmed_at=as_utc(record.confirmed_at),
        )
        for record, student in rows
    ]


@app.post(
    "/api/v1/attendance-sessions/{session_id}/rotate-code",
    response_model=AttendanceSessionResponse,
    tags=["professor"],
)
def rotate_code(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> AttendanceSessionResponse:
    """Issue a fresh code (e.g. after a page reload); the previous code and QR stop working."""
    session = owned_session(db, session_id, professor)
    if session.status != SessionStatus.OPEN or datetime.now(timezone.utc) >= as_utc(session.expires_at):
        raise HTTPException(status_code=409, detail="Attendance session is not open")
    code = make_code()
    session.code_hash = digest_code(code)
    session.code_version += 1
    db.add(AuditLog(actor_id=professor.id, action="attendance_code_rotated", entity_type="attendance_session", entity_id=session.id))
    db.commit()
    db.refresh(session)
    return session_credentials(session, code)


@app.post("/api/v1/attendance-sessions/{session_id}/close", tags=["professor"])
def close_attendance(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = owned_session(db, session_id, professor)
    if session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=409, detail="Attendance session is already closed")
    session.status = SessionStatus.CLOSED
    db.add(AuditLog(actor_id=professor.id, action="attendance_closed", entity_type="attendance_session", entity_id=session.id))
    db.commit()
    return {"status": "closed"}


def ensure_can_confirm(db: Session, session: AttendanceSession, student: User) -> None:
    if session.status != SessionStatus.OPEN:
        raise HTTPException(status_code=404, detail="Attendance session is not open")
    if datetime.now(timezone.utc) >= as_utc(session.expires_at):
        raise HTTPException(status_code=410, detail="Attendance session has expired")
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.section_id == session.section_id,
            Enrollment.student_id == student.id,
            Enrollment.status == "active",
        )
    )
    if enrollment is None:
        db.add(CodeAttempt(attendance_session_id=session.id, student_id=student.id, result="not_enrolled"))
        db.commit()
        raise HTTPException(status_code=403, detail="Student is not enrolled")


def record_attendance(
    db: Session,
    session: AttendanceSession,
    student: User,
    method: str,
    latitude: float | None,
    longitude: float | None,
) -> AttendanceResponse:
    location_required = session.location_latitude is not None
    if location_required and (latitude is None or longitude is None):
        raise HTTPException(status_code=422, detail="Location is required for this attendance session")
    if location_required and distance_meters(
        session.location_latitude,
        session.location_longitude,
        latitude,
        longitude,
    ) > session.allowed_radius_meters:
        raise HTTPException(status_code=403, detail="You are outside the attendance location")
    record = AttendanceRecord(
        attendance_session_id=session.id,
        student_id=student.id,
        confirmation_method=method,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(record)
    db.add(CodeAttempt(attendance_session_id=session.id, student_id=student.id, result="accepted"))
    db.add(AuditLog(actor_id=student.id, action="attendance_confirmed", entity_type="attendance_session", entity_id=session.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Attendance already confirmed") from exc
    db.refresh(record)
    section = db.get(CourseSection, session.section_id)
    return AttendanceResponse(
        id=record.id,
        session_id=record.attendance_session_id,
        section_name=section.name if section else "",
        title=session.title,
        status=record.status,
        confirmation_method=record.confirmation_method,
        confirmed_at=as_utc(record.confirmed_at),
    )


@app.post("/api/v1/attendance/confirm", response_model=AttendanceResponse, tags=["student"])
def confirm_attendance_by_code(
    payload: AttendanceCodeConfirmRequest,
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> AttendanceResponse:
    """Confirm with only the code shown in class; the open session is looked up from the code."""
    candidates = db.scalars(
        select(AttendanceSession).where(
            AttendanceSession.code_hash == digest_code(payload.code),
            AttendanceSession.status == SessionStatus.OPEN,
        )
    ).all()
    if not candidates:
        raise HTTPException(status_code=400, detail="Invalid attendance code")
    enrolled_section_ids = set(
        db.scalars(
            select(Enrollment.section_id).where(Enrollment.student_id == student.id, Enrollment.status == "active")
        ).all()
    )
    session = next((c for c in candidates if c.section_id in enrolled_section_ids), candidates[0])
    ensure_can_confirm(db, session, student)
    return record_attendance(db, session, student, "code", payload.latitude, payload.longitude)


@app.post("/api/v1/attendance-sessions/{session_id}/confirm", response_model=AttendanceResponse, tags=["student"])
def confirm_attendance(
    session_id: str,
    payload: AttendanceConfirmRequest,
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> AttendanceResponse:
    if not payload.code and not payload.qr_token:
        raise HTTPException(status_code=422, detail="Provide code or qr_token")
    session = db.get(AttendanceSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Attendance session is not open")
    ensure_can_confirm(db, session, student)
    method = "code"
    valid = bool(payload.code and code_matches(payload.code, session.code_hash))
    if payload.qr_token:
        method = "qr"
        try:
            qr = jwt.decode(payload.qr_token, get_settings().code_secret, algorithms=["HS256"])
            valid = valid or (
                qr.get("session_id") == session.id and qr.get("code_version") == session.code_version
            )
        except jwt.InvalidTokenError:
            valid = False
    if not valid:
        db.add(CodeAttempt(attendance_session_id=session.id, student_id=student.id, result="invalid"))
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid attendance code or QR")
    return record_attendance(db, session, student, method, payload.latitude, payload.longitude)


@app.post("/api/v1/attendance-sessions/{session_id}/cancel", tags=["professor"])
def cancel_attendance(
    session_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = owned_session(db, session_id, professor)
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
) -> AbsenceRequest:
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
    return absence


@app.get(
    "/api/v1/sections/{section_id}/absence-requests",
    response_model=list[AbsenceRequestResponse],
    tags=["absence"],
)
def section_absence_requests(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[AbsenceRequest]:
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    return db.scalars(
        select(AbsenceRequest)
        .where(AbsenceRequest.section_id == section_id)
        .order_by(AbsenceRequest.created_at.desc())
    ).all()


@app.patch("/api/v1/absence-requests/{request_id}", response_model=AbsenceRequestResponse, tags=["absence"])
def review_absence(
    request_id: str,
    payload: AbsenceReview,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> AbsenceRequest:
    absence = db.get(AbsenceRequest, request_id)
    section = db.get(CourseSection, absence.section_id) if absence else None
    if absence is None or section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Absence request not found")
    if absence.status != "pending":
        raise HTTPException(status_code=409, detail="Absence request was already reviewed")
    absence.status = payload.status
    db.add(
        Notification(
            user_id=absence.student_id,
            title="Absence request updated",
            message=f"Your absence request was {payload.status}.",
        )
    )
    db.add(AuditLog(actor_id=professor.id, action=f"absence_{payload.status}", entity_type="absence_request", entity_id=absence.id))
    db.commit()
    db.refresh(absence)
    return absence


@app.get("/api/v1/notifications", response_model=list[NotificationResponse], tags=["notifications"])
def notifications(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Notification]:
    return db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100)
    ).all()


@app.get("/api/v1/students/me/statistics", response_model=StudentStatsResponse, tags=["statistics"])
def student_statistics(
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> StudentStatsResponse:
    sessions = db.scalars(
        select(AttendanceSession)
        .join(Enrollment, Enrollment.section_id == AttendanceSession.section_id)
        .where(
            Enrollment.student_id == student.id,
            Enrollment.status == "active",
            AttendanceSession.status != SessionStatus.CANCELLED,
        )
        .order_by(AttendanceSession.starts_at.desc())
    ).all()
    section_names = {
        section.id: section.name
        for section in db.scalars(
            select(CourseSection).where(CourseSection.id.in_({session.section_id for session in sessions}))
        ).all()
    }
    records = {
        record.attendance_session_id: record
        for record in db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student.id)).all()
    }
    session_ids = {session.id for session in sessions}
    records = {session_id: record for session_id, record in records.items() if session_id in session_ids}
    present = sum(1 for record in records.values() if record.status == "present")
    late = sum(1 for record in records.values() if record.status == "late")
    by_session = [
        {
            "session_id": session.id,
            "section_id": session.section_id,
            "section_name": section_names.get(session.section_id, ""),
            "title": session.title,
            "date": session.session_date.isoformat(),
            "starts_at": as_utc(session.starts_at).isoformat(),
            "status": records[session.id].status if session.id in records else "absent",
            "confirmed_at": as_utc(records[session.id].confirmed_at).isoformat() if session.id in records else None,
        }
        for session in sessions
    ]
    total = len(sessions)
    attended = present + late
    return StudentStatsResponse(
        student_id=student.id,
        total_sessions=total,
        attended_sessions=attended,
        attendance_percentage=round(attended * 100 / total, 2) if total else 0,
        present=present,
        late=late,
        absent=total - attended,
        by_session=by_session,
    )


@app.get("/api/v1/sections/{section_id}/statistics", response_model=ClassStatsResponse, tags=["statistics"])
def class_statistics(
    section_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> ClassStatsResponse:
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    sessions = db.scalars(
        select(AttendanceSession)
        .where(AttendanceSession.section_id == section_id, AttendanceSession.status != SessionStatus.CANCELLED)
        .order_by(AttendanceSession.session_date)
    ).all()
    enrolled_count = len(
        db.scalars(select(Enrollment).where(Enrollment.section_id == section_id, Enrollment.status == "active")).all()
    )
    records = db.scalars(
        select(AttendanceRecord)
        .join(AttendanceSession)
        .where(AttendanceSession.section_id == section_id, AttendanceSession.status != SessionStatus.CANCELLED)
    ).all()
    records_by_session: dict[str, int] = {}
    for record in records:
        records_by_session[record.attendance_session_id] = records_by_session.get(record.attendance_session_id, 0) + 1
    total_possible = len(sessions) * enrolled_count
    by_session = [
        {
            "session_id": session.id,
            "date": session.session_date.isoformat(),
            "confirmed": records_by_session.get(session.id, 0),
            "enrolled": enrolled_count,
            "attendance_percentage": round(records_by_session.get(session.id, 0) * 100 / enrolled_count, 2)
            if enrolled_count
            else 0,
        }
        for session in sessions
    ]
    return ClassStatsResponse(
        section_id=section_id,
        total_sessions=len(sessions),
        enrolled_students=enrolled_count,
        total_confirmations=len(records),
        attendance_percentage=round(len(records) * 100 / total_possible, 2) if total_possible else 0,
        by_session=by_session,
    )
