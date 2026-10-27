"""Load class lists from the department's CSV export into the database.

Usage: python -m app.roster path/to/list.csv

The export has no emails and no professor, so students get placeholder accounts that
cannot sign in, and every section is owned by one inactive placeholder professor.
"""

import csv
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, ensure_additive_schema
from .models import CourseSection, Enrollment, User
from .security import hash_password

ROSTER_OWNER_EMAIL = "roster-owner@roster.example.org"
PLACEHOLDER_DOMAIN = "roster.example.org"
NAME_HEADERS = {"Nume", "Numele și prenumele studentului"}
GROUP_HEADER = "Grupa"


@dataclass(frozen=True)
class RosterRow:
    section: str
    last_name: str
    first_name: str


def split_name(full_name: str) -> tuple[str, str]:
    """Split 'AL-KHALIDY R. Essam' into surname and given name.

    Surnames are written in capitals in these lists, so leading all-caps tokens form the surname.
    """
    tokens = full_name.replace(",", " ").split()
    last = []
    while tokens and tokens[0].isupper():
        last.append(tokens.pop(0))
    if not last:  # no capitalised surname, keep the first word as the surname
        last, tokens = tokens[:1], tokens[1:]
    return " ".join(last), " ".join(tokens)


def read_roster(path: Path) -> list[RosterRow]:
    """Parse every block of the export: each block has its own header row and column layout."""
    rows: list[RosterRow] = []
    name_col: int | None = None
    group_col: int | None = None
    section: str | None = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for cells in csv.reader(handle):
            cells = [cell.strip() for cell in cells]
            if not any(cells):
                continue
            if cells[0] in ("NrCrt", "Nr Crt"):
                name_col = next((i for i, cell in enumerate(cells) if cell in NAME_HEADERS), None)
                group_col = cells.index(GROUP_HEADER) if GROUP_HEADER in cells else None
                section = None
                continue
            if name_col is None:
                continue
            if cells[0].startswith("Group ") and not any(cells[1:]):
                section = cells[0].removeprefix("Group ").strip()
                continue
            name = cells[name_col] if name_col < len(cells) else ""
            if not name:
                continue
            group = cells[group_col] if group_col is not None and group_col < len(cells) else ""
            row_section = group or section
            if not row_section:
                raise ValueError(f"Student without a group: {name}")
            last_name, first_name = split_name(name)
            rows.append(RosterRow(section=row_section, last_name=last_name, first_name=first_name))
    return rows


def placeholder_email(last_name: str, first_name: str) -> str:
    """Same person in two groups gets one account: the address is derived from the name."""
    decomposed = unicodedata.normalize("NFKD", f"{last_name} {first_name}".casefold())
    ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", ".", ascii_name).strip(".")
    return f"{slug}@{PLACEHOLDER_DOMAIN}"


def owner_account(db: Session) -> User:
    owner = db.scalar(select(User).where(User.email == ROSTER_OWNER_EMAIL))
    if owner is None:
        owner = User(
            email=ROSTER_OWNER_EMAIL,
            # Inactive: nobody can sign in as this placeholder. The password is a random secret nobody knows.
            password_hash=hash_password(secrets.token_urlsafe(32)),
            first_name="Roster",
            last_name="Import",
            role="professor",
            is_active=False,
        )
        db.add(owner)
        db.flush()
    return owner


def import_roster(db: Session, rows: list[RosterRow]) -> dict[str, int]:
    """Create sections, students, and enrollments. Running it again changes nothing that already exists."""
    owner = owner_account(db)
    counts = {"sections": 0, "students": 0, "enrollments": 0}
    sections: dict[str, CourseSection] = {}
    students: dict[str, User] = {}
    for row in rows:
        section = sections.get(row.section)
        if section is None:
            section = db.scalar(
                select(CourseSection).where(CourseSection.name == row.section, CourseSection.professor_id == owner.id)
            )
            if section is None:
                section = CourseSection(name=row.section, professor_id=owner.id)
                db.add(section)
                db.flush()
                counts["sections"] += 1
            sections[row.section] = section

        email = placeholder_email(row.last_name, row.first_name)
        student = students.get(email) or db.scalar(select(User).where(User.email == email))
        if student is None:
            student = User(
                email=email,
                password_hash=hash_password(secrets.token_urlsafe(32)),
                first_name=row.first_name,
                last_name=row.last_name,
                role="student",
                is_active=False,
            )
            db.add(student)
            db.flush()
            counts["students"] += 1
        students[email] = student

        enrolled = db.scalar(
            select(Enrollment).where(Enrollment.section_id == section.id, Enrollment.student_id == student.id)
        )
        if enrolled is None:
            db.add(Enrollment(section_id=section.id, student_id=student.id))
            counts["enrollments"] += 1
    db.commit()
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m app.roster path/to/list.csv", file=sys.stderr)
        return 2
    rows = read_roster(Path(argv[1]))
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema()
    with SessionLocal() as db:
        counts = import_roster(db, rows)
    print(f"Read {len(rows)} student rows. Created: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
