import base64
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AttendanceRecord, AttendanceSession, AttendanceStatus, AuditLog, CourseSection, Enrollment, SessionStatus, User
from .recognition import analyse, annotate, read_boxes, roster_student
from .security import digest_code, make_code, require_role

router = APIRouter(prefix="/api/v1/sections/{section_id}/attendance-scans", tags=["professor"])
students_router = APIRouter(prefix="/api/v1", tags=["professor"])

# Image types only: the sheet is read, never kept.
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


class ScanConfirmSession(BaseModel):
    label: str | None = Field(default=None, max_length=20)
    starts_at: datetime | None = None
    student_ids: list[str] = Field(default_factory=list, max_length=500)


class ScanConfirm(BaseModel):
    session_date: date
    sessions: list[ScanConfirmSession] = Field(min_length=1, max_length=10)


def professor_students(db: Session, professor: User) -> list[tuple[User, list[str]]]:
    """Every student in any of the professor's classes, with the names of those classes.

    Students can attend labs of other groups, so a sheet is matched against all of them.
    """
    rows = db.execute(
        select(User, CourseSection.name)
        .join(Enrollment, Enrollment.student_id == User.id)
        .join(CourseSection, CourseSection.id == Enrollment.section_id)
        .where(CourseSection.professor_id == professor.id, Enrollment.status == "active")
        .order_by(User.last_name, User.first_name)
    ).all()
    grouped: dict[str, tuple[User, list[str]]] = {}
    for student, class_name in rows:
        grouped.setdefault(student.id, (student, []))[1].append(class_name)
    return list(grouped.values())


@students_router.get("/students")
def all_students(
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Every student in the professor's classes, with the classes each one is in."""
    return [
        {
            "id": student.id,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "email": student.email,
            "classes": classes,
        }
        for student, classes in professor_students(db, professor)
    ]


@router.post("")
def preview_scan(
    section_id: str,
    file: UploadFile = File(...),
    session_date: date | None = Form(default=None),
    annotate_sheet: bool = Form(default=False),
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict:
    """Read a photo of an attendance sheet and return the names it matches. Nothing is saved; confirm to record.

    With annotate_sheet, the response also carries the photo with red rectangles around rows that need a look.
    """
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    limit = get_settings().max_upload_bytes
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File is too large. The limit is {limit // (1024 * 1024)} MB.")
    if not any(content.startswith(signature) for signature in IMAGE_SIGNATURES):
        raise HTTPException(status_code=415, detail="Upload a PNG or JPEG photo of the attendance sheet")
    roster = [
        roster_student(student.id, student.first_name, student.last_name, classes)
        for student, classes in professor_students(db, professor)
    ]
    result = analyse(read_boxes(content), roster, session_date, default_year=date.today().year)
    confirmed = result.pop("confirmed")
    flagged = result.pop("flagged")
    if annotate_sheet:
        picture = base64.b64encode(annotate(content, confirmed, flagged)).decode("ascii")
        result["annotated_image"] = f"data:image/png;base64,{picture}"
    return result


@router.post("/confirm", status_code=201)
def confirm_scan(
    section_id: str,
    payload: ScanConfirm,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> dict:
    """Record the students the professor kept from a scan, one attendance session per sheet part."""
    section = db.get(CourseSection, section_id)
    if section is None or section.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Section not found")
    known = {student.id for student, _ in professor_students(db, professor)}
    for entry in payload.sessions:
        unknown = set(entry.student_ids) - known
        if unknown:
            raise HTTPException(status_code=422, detail="Some students are not in any of your classes")
    created = []
    for entry in payload.sessions:
        starts_at = entry.starts_at or datetime.combine(payload.session_date, time.min, tzinfo=timezone.utc)
        session = AttendanceSession(
            section_id=section_id,
            opened_by=professor.id,
            session_date=payload.session_date,
            starts_at=starts_at,
            expires_at=starts_at,
            status=SessionStatus.CLOSED,
            code_hash=digest_code(make_code()),
        )
        db.add(session)
        db.flush()
        for student_id in dict.fromkeys(entry.student_ids):
            db.add(
                AttendanceRecord(
                    attendance_session_id=session.id,
                    student_id=student_id,
                    status=AttendanceStatus.PRESENT,
                    confirmation_method="scan",
                    confirmed_at=starts_at,
                )
            )
        db.add(AuditLog(actor_id=professor.id, action="attendance_scanned", entity_type="attendance_session", entity_id=session.id))
        created.append({"label": entry.label, "session_id": session.id, "marked": len(set(entry.student_ids))})
    db.commit()
    return {"sessions": created}
