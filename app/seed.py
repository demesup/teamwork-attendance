"""Demo data for local development. Runs on startup when SEED_DEMO_DATA=true; safe to run repeatedly."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import SessionLocal
from .models import AttendanceRecord, AttendanceSession, CourseSection, Enrollment, SessionStatus, User
from .security import digest_code, hash_password, make_code

DEMO_PASSWORD = "demo-password-123"
PROFESSOR = ("elena.ionescu@university.edu", "Elena", "Ionescu")
STUDENTS = [
    ("alex.popescu@student.university.edu", "Alex", "Popescu"),
    ("maria.ionescu@student.university.edu", "Maria", "Ionescu"),
    ("andrei.pavel@student.university.edu", "Andrei", "Pavel"),
    ("sofia.dumitru@student.university.edu", "Sofia", "Dumitru"),
    ("david.radu@student.university.edu", "David", "Radu"),
]
SECTIONS = {
    "Digital Electronics": ["Logic Gates & Boolean Algebra", "Combinational Circuits"],
    "Data Structures": ["Linked Lists", "Binary Search Trees"],
    "Computer Networks": ["TCP/IP Protocols"],
}


def seed_demo_data() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == PROFESSOR[0])):
            return
        password_hash = hash_password(DEMO_PASSWORD)
        professor = User(
            email=PROFESSOR[0], first_name=PROFESSOR[1], last_name=PROFESSOR[2], role="professor", password_hash=password_hash
        )
        students = [
            User(email=email, first_name=first, last_name=last, role="student", password_hash=password_hash)
            for email, first, last in STUDENTS
        ]
        db.add_all([professor, *students])
        db.flush()

        now = datetime.now(timezone.utc)
        days_ago = 1
        for section_name, labs in SECTIONS.items():
            section = CourseSection(name=section_name, professor_id=professor.id)
            db.add(section)
            db.flush()
            db.add_all(Enrollment(section_id=section.id, student_id=student.id) for student in students)
            for title in labs:
                starts_at = now - timedelta(days=days_ago)
                days_ago += 2
                session = AttendanceSession(
                    section_id=section.id,
                    opened_by=professor.id,
                    title=title,
                    session_date=starts_at.date(),
                    starts_at=starts_at,
                    expires_at=starts_at + timedelta(minutes=15),
                    status=SessionStatus.CLOSED,
                    code_hash=digest_code(make_code()),
                )
                db.add(session)
                db.flush()
                # Everyone attends except one rotating absentee, so stats look realistic.
                absentee = students[days_ago % len(students)]
                db.add_all(
                    AttendanceRecord(
                        attendance_session_id=session.id,
                        student_id=student.id,
                        confirmation_method="code",
                        confirmed_at=starts_at + timedelta(minutes=2 + index),
                    )
                    for index, student in enumerate(students)
                    if student is not absentee
                )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    from .db import Base, engine, ensure_additive_schema

    Base.metadata.create_all(bind=engine)
    ensure_additive_schema()
    seed_demo_data()
    print("Demo data ready.")
