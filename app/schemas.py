from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern="^(professor|student)$")


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


class SectionSummary(SectionResponse):
    professor_name: str
    enrolled_students: int
    total_sessions: int
    attended_sessions: int | None = None


class AttendanceSessionCreate(BaseModel):
    section_id: str
    title: str | None = Field(default=None, max_length=200)
    duration_minutes: int = Field(default=15, ge=1, le=120)
    location_latitude: float | None = Field(default=None, ge=-90, le=90)
    location_longitude: float | None = Field(default=None, ge=-180, le=180)
    allowed_radius_meters: int | None = Field(default=None, ge=10, le=1000)


class AttendanceSessionResponse(BaseModel):
    id: str
    section_id: str
    title: str | None
    expires_at: datetime
    code: str
    qr_token: str
    location_required: bool


class AttendanceSessionSummary(BaseModel):
    id: str
    section_id: str
    section_name: str
    title: str | None
    status: str
    session_date: date
    starts_at: datetime
    expires_at: datetime
    location_required: bool
    confirmed_count: int
    enrolled_count: int


class AttendanceRecordRow(BaseModel):
    id: str
    student_id: str
    first_name: str
    last_name: str
    email: str
    status: str
    confirmation_method: str
    confirmed_at: datetime


class AttendanceCodeConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class AttendanceConfirmRequest(BaseModel):
    code: str | None = Field(default=None, min_length=6, max_length=64)
    qr_token: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class AttendanceResponse(BaseModel):
    id: str
    session_id: str
    section_name: str
    title: str | None
    status: str
    confirmation_method: str
    confirmed_at: datetime


class EnrollmentCreate(BaseModel):
    student_id: str | None = None
    student_email: EmailStr | None = None


class StudentStatsResponse(BaseModel):
    student_id: str
    total_sessions: int
    attended_sessions: int
    attendance_percentage: float
    present: int
    late: int
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
    attachment_url: str | None = Field(default=None, max_length=2000)


class AbsenceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    student_id: str
    section_id: str
    attendance_session_id: str | None
    reason: str
    status: str
    created_at: datetime


class AbsenceReview(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    message: str
    read_at: datetime | None
    created_at: datetime
