from app.db import SessionLocal
from app.models import CourseSection, Enrollment, User
from app.roster import import_roster, placeholder_email, read_roster, split_name

CSV = """Group 1241EA,,,,
NrCrt,Nume,Grupa,,,
1,BĂITAN D. Raluca-Mihaela,1241EA,,,
2,AL SALMAN Ahmad Ayman,1241EA,,,
3,TAKDEER S M,1241EA,,,
,,,,,
Nr Crt,Grupa,Numele și prenumele studentului,,
1,Grupa 1280,"CASADO ESCURA, Javier",,
"""


def test_surname_is_the_leading_capitals():
    assert split_name("BĂITAN D. Raluca-Mihaela") == ("BĂITAN D.", "Raluca-Mihaela")
    assert split_name("AL SALMAN Ahmad Ayman") == ("AL SALMAN", "Ahmad Ayman")
    assert split_name("TAKDEER S M") == ("TAKDEER S M", "")


def test_read_roster_handles_each_block_layout(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text(CSV, encoding="utf-8")
    rows = read_roster(path)
    assert [(row.section, row.last_name) for row in rows] == [
        ("1241EA", "BĂITAN D."),
        ("1241EA", "AL SALMAN"),
        ("1241EA", "TAKDEER S M"),
        ("Grupa 1280", "CASADO ESCURA"),
    ]


def test_placeholder_emails_drop_accents_and_stay_valid():
    assert placeholder_email("ȚINȚĂREANU-MIRCEA M.I.", "Mihai-Cezar") == "tintareanu.mircea.m.i.mihai.cezar@roster.example.org"


def test_import_creates_students_once(client, tmp_path):
    path = tmp_path / "list.csv"
    path.write_text(CSV, encoding="utf-8")
    rows = read_roster(path)
    with SessionLocal() as db:
        first = import_roster(db, rows)
        second = import_roster(db, rows)
        assert first == {"sections": 2, "students": 4, "enrollments": 4}
        assert second == {"sections": 0, "students": 0, "enrollments": 0}
        assert db.query(Enrollment).count() == 4
        assert db.query(CourseSection).count() == 2
        assert db.query(User).filter(User.role == "student", User.is_active.is_(True)).count() == 0
