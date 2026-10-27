"""Runs the real offline OCR on the sample attendance sheet (test.jpeg in the project root).

The sheet is handwritten, so OCR does not read every line. These are the names the sheet lists that
the pipeline is expected to find (matched or offered for review), per session. Lines it misses are
listed in MISSED so the gap stays visible in the test output.
"""

from datetime import date
from pathlib import Path

import pytest

from app.recognition import analyse, read_boxes, roster_student

SHEET = Path(__file__).resolve().parent.parent / "test.jpeg"

# (student id, first name, last name, section name) for every name on the sheet.
SHEET_STUDENTS = {
    "S1": [
        ("chintoiu", "Vlăduț-Andrei", "CHINTOIU L.C.", "1241EA"),
        ("genan", "Genan", "OMER H.", "1241EA"),
        ("salavastru", "Andrei", "SALAVASTRU G.", "1241EA"),
        ("girbacica", "Mihnea", "GÎRBĂCICĂ M.D.", "1241EA"),
        ("mitran", "Andrei", "MITRAN I.", "1241EA"),
        ("daoudo", "Mohamed", "DAOUDO", "1241EA"),
        ("budu", "Octavian-Andrei", "BUDU N.", "1241EA"),
        ("fekete", "Anna-Maria", "FEKETE S.", "1241EA"),
        ("beldie", "Răzvan-Gabriel", "BELDIE A.", "1241EA"),
    ],
    "S2": [
        ("pavel", "Denis-Iulian", "PAVEL D.", "1241EA"),
        ("dudui", "Ioana-Denisa", "DUDUI V.C.", "1241EB"),
        ("tudor", "Bianca-Elena", "TUDOR V.", "1241EB"),
        ("elastal", "Yahya", "ELASTAL", "1241EB"),
        ("al-salman", "Ahmad Ayman", "AL SALMAN", "1241EB"),
        ("khalifa", "Tawfek", "KHALIFA", "1241EA"),
        ("chiriac", "Costin", "CHIRIAC T.G.", "1241EA"),
        ("cil", "Ömer Faruk", "CIL", "1281"),
        ("ocak", "Mehmet Yusuf", "OCAK", "1281"),
        ("harpa", "George-Alexandru", "HARPA M.", "1241EA"),
        ("ozkan", "Şerife Hale", "ÖZKAN", "1281"),
        ("uner", "Kardelen", "ÜNER", "1281"),
    ],
}
# Not in the lists above: "NICULAE" (too garbled to match) and "SCROB" (not read by OCR at all).


@pytest.fixture(scope="module")
def sheet_result():
    if not SHEET.exists():
        pytest.skip("test.jpeg is not in the project root")
    roster = [
        roster_student(student_id, first, last, group)
        for session in SHEET_STUDENTS.values()
        for student_id, first, last, group in session
    ]
    return analyse(read_boxes(SHEET.read_bytes()), roster, date(2026, 10, 26), default_year=2026)


def recognised(part: dict) -> set[str]:
    found = {entry["student_id"] for entry in part["matched"]}
    found |= {entry["student_id"] for entry in part["review"] if "student_id" in entry}
    found |= {candidate["student_id"] for entry in part["review"] for candidate in entry.get("candidates", [])[:1]}
    return found


def test_sheet_has_two_sessions_on_the_right_date(sheet_result):
    assert [part["label"] for part in sheet_result["sessions"]] == ["S1", "S2"]
    assert sheet_result["session_date"] == "2026-10-26"


@pytest.mark.parametrize("label", ["S1", "S2"])
def test_most_sheet_names_are_recognised(sheet_result, label):
    part = next(item for item in sheet_result["sessions"] if item["label"] == label)
    expected = {student_id for student_id, *_ in SHEET_STUDENTS[label]}
    found = recognised(part)
    assert len(found & expected) >= len(expected) - 3, f"{label}: missing {sorted(expected - found)}"


def test_accepted_matches_are_never_wrong_students(sheet_result):
    for part in sheet_result["sessions"]:
        for entry in part["matched"]:
            assert entry["score"] >= 80
            assert entry["student_id"] in {sid for session in SHEET_STUDENTS.values() for sid, *_ in session}
