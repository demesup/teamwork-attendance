import pytest

from app import attendance_scan
from app.db import SessionLocal
from app.models import AttendanceRecord, AttendanceSession
from app.recognition import OcrBox

PNG = b"\x89PNG\r\n\x1a\n" + b"fake image body"


@pytest.fixture
def fake_ocr(monkeypatch):
    """Stand in for the OCR engine: one row per enrolled name, nothing is read from disk."""
    calls = []

    def fake_read_boxes(content: bytes):
        calls.append(content)
        return [OcrBox(text="Test Student", x=0, y=10, height=20)]

    monkeypatch.setattr(attendance_scan, "read_boxes", fake_read_boxes)
    return calls


def upload(client, section_id, actor, content=PNG, name="sheet.png", **form):
    return client.post(
        f"/api/v1/sections/{section_id}/attendance-scans",
        files={"file": (name, content, "image/png")},
        data=form,
        headers=actor["headers"],
    )


def test_professor_gets_a_preview_and_nothing_is_saved(client, professor, section, student, fake_ocr):
    response = upload(client, section, professor, session_date="2026-10-26")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_date"] == "2026-10-26"
    assert fake_ocr == [PNG]
    with SessionLocal() as db:
        assert db.query(AttendanceSession).count() == 0
        assert db.query(AttendanceRecord).count() == 0


def test_only_the_owning_professor_can_scan(client, other_professor, section, fake_ocr):
    assert upload(client, section, other_professor).status_code == 404


def test_students_cannot_scan(client, student, section, fake_ocr):
    assert upload(client, section, student).status_code == 403


def test_non_image_files_are_refused(client, professor, section, fake_ocr):
    response = upload(client, section, professor, content=b"%PDF-1.4 not an image")
    assert response.status_code == 415
    assert fake_ocr == []


def test_oversized_files_are_refused(client, professor, section, fake_ocr, monkeypatch):
    monkeypatch.setattr(attendance_scan, "get_settings", lambda: type("S", (), {"max_upload_bytes": 10})())
    assert upload(client, section, professor, content=PNG).status_code == 413


def test_confirm_records_each_session_and_rejects_unknown_students(client, professor, section, student, other_student, outsider):
    enroll_ids = {"student": student["id"], "other": other_student["id"]}
    body = {
        "session_date": "2026-10-26",
        "sessions": [
            {"label": "S1", "student_ids": [enroll_ids["student"]]},
            {"label": "S2", "student_ids": [enroll_ids["student"], enroll_ids["other"]]},
        ],
    }
    response = client.post(f"/api/v1/sections/{section}/attendance-scans/confirm", json=body, headers=professor["headers"])
    assert response.status_code == 201, response.text
    created = response.json()["sessions"]
    assert [item["marked"] for item in created] == [1, 2]
    with SessionLocal() as db:
        assert db.query(AttendanceSession).count() == 2
        assert {record.confirmation_method for record in db.query(AttendanceRecord).all()} == {"scan"}

    bad = {"session_date": "2026-10-26", "sessions": [{"label": "S1", "student_ids": [outsider["id"]]}]}
    rejected = client.post(f"/api/v1/sections/{section}/attendance-scans/confirm", json=bad, headers=professor["headers"])
    assert rejected.status_code == 422


def test_preview_can_return_the_photo_with_red_marks(client, professor, section, fake_ocr):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 150), (255, 255, 255)).save(buffer, format="PNG")
    response = upload(client, section, professor, content=buffer.getvalue(), annotate_sheet="true")
    assert response.status_code == 200, response.text
    assert response.json()["annotated_image"].startswith("data:image/png;base64,")
    assert "flagged" not in response.json()
