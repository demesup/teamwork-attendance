from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Role(StrEnum):
    PROFESSOR = "professor"
    STUDENT = "student"


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CourseSection(Base):
    __tablename__ = "course_sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    professor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    professor: Mapped[User] = relationship()


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("section_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")


class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    opened_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(200))
    session_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=SessionStatus.OPEN)
    code_hash: Mapped[str] = mapped_column(Text)
    code_version: Mapped[int] = mapped_column(Integer, default=1)
    location_latitude: Mapped[float | None] = mapped_column(Float)
    location_longitude: Mapped[float | None] = mapped_column(Float)
    allowed_radius_meters: Mapped[int | None] = mapped_column(Integer)


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("attendance_session_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attendance_session_id: Mapped[str] = mapped_column(ForeignKey("attendance_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=AttendanceStatus.PRESENT)
    confirmation_method: Mapped[str] = mapped_column(String(20))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)


class CodeAttempt(Base):
    __tablename__ = "attendance_code_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attendance_session_id: Mapped[str] = mapped_column(ForeignKey("attendance_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    result: Mapped[str] = mapped_column(String(30))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AbsenceRequest(Base):
    __tablename__ = "absence_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    attendance_session_id: Mapped[str | None] = mapped_column(ForeignKey("attendance_sessions.id"))
    reason: Mapped[str] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
