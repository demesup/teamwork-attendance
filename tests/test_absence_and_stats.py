from datetime import datetime, timedelta, timezone

from app.models import AttendanceSession
from tests.helpers import confirm, create_section, open_session


def submit_absence(client, student, section_id, **extra):
    body = {"section_id": section_id, "reason": "Medical appointment today", **extra}
    return client.post("/api/v1/absence-requests", json=body, headers=student["headers"])


def test_enrolled_student_submits_absence_request(client, student, section):
    response = submit_absence(client, student, section)
    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_unenrolled_student_cannot_submit_absence(client, outsider, section):
    assert submit_absence(client, outsider, section).status_code == 403


def test_absence_reason_must_be_at_least_10_chars(client, student, section):
    response = client.post(
        "/api/v1/absence-requests",
        json={"section_id": section, "reason": "sick"},
        headers=student["headers"],
    )
    assert response.status_code == 422


def test_absence_for_session_of_another_section_is_rejected(client, professor, student, section):
    other_section = create_section(client, professor, name="Other")
    other_session = open_session(client, professor, other_section)
    response = submit_absence(client, student, section, attendance_session_id=other_session["id"])
    assert response.status_code == 404


def test_professor_approves_and_student_is_notified(client, professor, student, section):
    request_id = submit_absence(client, student, section).json()["id"]
    response = client.patch(
        f"/api/v1/absence-requests/{request_id}",
        json={"status": "approved"},
        headers=professor["headers"],
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    notes = client.get("/api/v1/notifications", headers=student["headers"]).json()
    assert any(note["title"] == "Absence request updated" for note in notes)


def test_review_is_final(client, professor, student, section):
    request_id = submit_absence(client, student, section).json()["id"]
    client.patch(f"/api/v1/absence-requests/{request_id}", json={"status": "rejected"}, headers=professor["headers"])
    response = client.patch(
        f"/api/v1/absence-requests/{request_id}", json={"status": "approved"}, headers=professor["headers"]
    )
    assert response.status_code == 409


def test_review_status_must_be_approved_or_rejected(client, professor, student, section):
    request_id = submit_absence(client, student, section).json()["id"]
    response = client.patch(
        f"/api/v1/absence-requests/{request_id}", json={"status": "pending"}, headers=professor["headers"]
    )
    assert response.status_code == 422


def test_only_section_owner_can_review_or_list(client, other_professor, professor, student, section):
    request_id = submit_absence(client, student, section).json()["id"]
    review = client.patch(
        f"/api/v1/absence-requests/{request_id}", json={"status": "approved"}, headers=other_professor["headers"]
    )
    assert review.status_code == 404
    listing = client.get(f"/api/v1/sections/{section}/absence-requests", headers=other_professor["headers"])
    assert listing.status_code == 404


def test_professor_lists_requests_for_their_section(client, professor, student, section):
    submit_absence(client, student, section)
    listing = client.get(f"/api/v1/sections/{section}/absence-requests", headers=professor["headers"])
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_student_statistics_reflect_attendance(client, professor, student, section):
    first = open_session(client, professor, section)
    missed = open_session(client, professor, section)
    confirm(client, student, first["id"], code=first["code"])
    # The missed session must end before it counts as an absence.
    client.post(f"/api/v1/attendance-sessions/{missed['id']}/close", headers=professor["headers"])

    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["total_sessions"] == 2
    assert stats["attended_sessions"] == 1
    assert stats["present"] == 1
    assert stats["attendance_percentage"] == 50.0


def test_student_with_no_sessions_has_zero_percent(client, student):
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["attendance_percentage"] == 0
    assert stats["total_sessions"] == 0


def test_cancelled_session_is_not_counted_in_class_statistics(client, professor, student, section):
    session = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{session['id']}/cancel", headers=professor["headers"])
    stats = client.get(f"/api/v1/sections/{section}/statistics", headers=professor["headers"]).json()
    assert stats["total_sessions"] == 0


def test_class_statistics_count_confirmations(client, professor, student, other_student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    # Class statistics only include sessions that have finished.
    client.post(f"/api/v1/attendance-sessions/{session['id']}/close", headers=professor["headers"])
    stats = client.get(f"/api/v1/sections/{section}/statistics", headers=professor["headers"]).json()
    assert stats["enrolled_students"] == 2
    assert stats["total_confirmations"] == 1
    assert stats["attendance_percentage"] == 50.0
    assert stats["by_session"][0]["confirmed"] == 1


def test_class_statistics_only_for_owner(client, other_professor, section):
    response = client.get(f"/api/v1/sections/{section}/statistics", headers=other_professor["headers"])
    assert response.status_code == 404


def test_cancelled_session_is_not_counted_as_absence(client, professor, student, section):
    session = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{session['id']}/cancel", headers=professor["headers"])
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["total_sessions"] == 0


def test_expired_unconfirmed_session_counts_as_absent(client, professor, student, section, db):
    session = open_session(client, professor, section)
    row = db.get(AttendanceSession, session["id"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["absent"] == 1
