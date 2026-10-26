from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator

# SQLite hands back naive datetimes. Every stored value is UTC, so label it as such;
# otherwise browsers parse the timestamp as local time and countdowns end early.
UtcDatetime = Annotated[datetime, AfterValidator(lambda value: value if value.tzinfo else value.replace(tzinfo=timezone.utc))]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern="^(professor|student)$")
    professor_code: str | None = Field(default=None, max_length=200)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    first_name: str
    last_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SectionResponse(SectionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    professor_id: str


class OpenSessionSummary(BaseModel):
    id: str
    expires_at: UtcDatetime
    location_required: bool
    already_confirmed: bool = False


class SectionSummary(BaseModel):
    id: str
    name: str
    professor_id: str
    professor_name: str
    student_count: int
    open_session: OpenSessionSummary | None


class SessionSummary(BaseModel):
    id: str
    section_id: str
    session_date: str
    starts_at: UtcDatetime
    expires_at: UtcDatetime
    status: str
    confirmed: int
    # Students enrolled when the session started.
    eligible: int
    location_required: bool
    late_after_minutes: int | None = None


class AttendanceSessionCreate(BaseModel):
    section_id: str
    duration_minutes: int = Field(default=15, ge=1, le=120)
    location_latitude: float | None = Field(default=None, ge=-90, le=90)
    location_longitude: float | None = Field(default=None, ge=-180, le=180)
    allowed_radius_meters: int | None = Field(default=None, ge=10, le=1000)
    # Confirmations this many minutes after the start are recorded as late. None means never late.
    late_after_minutes: int | None = Field(default=None, ge=1, le=120)


class AttendanceSessionResponse(BaseModel):
    id: str
    section_id: str
    expires_at: UtcDatetime
    code: str
    qr_token: str
    location_required: bool


class QrTokenResponse(BaseModel):
    qr_token: str
    expires_at: UtcDatetime


class CodeRotationResponse(QrTokenResponse):
    code: str
    code_version: int


class AttendanceConfirmRequest(BaseModel):
    code: str | None = Field(default=None, min_length=6, max_length=64)
    qr_token: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class AttendanceResponse(BaseModel):
    id: str
    session_id: str
    status: str
    confirmation_method: str
    confirmed_at: UtcDatetime


class EnrollmentCreate(BaseModel):
    """Identify the student by id or, more conveniently, by email."""

    student_id: str | None = None
    email: EmailStr | None = None

    @model_validator(mode="after")
    def one_identifier(self) -> "EnrollmentCreate":
        if not self.student_id and not self.email:
            raise ValueError("Provide student_id or email")
        return self


class StudentSummary(BaseModel):
    id: str
    email: EmailStr
    first_name: str
    last_name: str


class EnrollmentResponse(BaseModel):
    status: str
    student: StudentSummary


class AttendanceOverride(BaseModel):
    status: str = Field(pattern="^(present|late|excused|absent)$")


class RosterEntry(BaseModel):
    student: StudentSummary
    # present, late, excused, absent, or pending while the session is still open
    status: str
    confirmation_method: str | None
    confirmed_at: UtcDatetime | None


class AttendanceRecordSummary(BaseModel):
    student: StudentSummary
    status: str
    confirmation_method: str
    confirmed_at: UtcDatetime


class StudentStatsResponse(BaseModel):
    student_id: str
    total_sessions: int
    attended_sessions: int
    # Excused sessions are left out of the percentage.
    attendance_percentage: float
    present: int
    late: int
    excused: int = 0
    absent: int
    by_session: list[dict]


class ClassStatsResponse(BaseModel):
    section_id: str
    total_sessions: int
    enrolled_students: int
    total_confirmations: int
    attendance_percentage: float
    by_session: list[dict]


class AbsenceRequestCreate(BaseModel):
    section_id: str
    attendance_session_id: str | None = None
    reason: str = Field(min_length=10, max_length=2000)
    attachment_url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")


class AbsenceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    student_id: str
    section_id: str
    attendance_session_id: str | None
    reason: str
    attachment_url: str | None = None
    attachment_name: str | None = None
    has_attachment: bool = False
    status: str
    created_at: UtcDatetime
    student_name: str | None = None
    section_name: str | None = None
    session_date: str | None = None


class AbsenceReview(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    message: str
    read_at: UtcDatetime | None
    created_at: UtcDatetime
