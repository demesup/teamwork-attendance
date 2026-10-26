"""Fixes for the problems listed in the external review (PROBLEMS.md / plan.md)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.main import check_production_settings
from app.models import AttendanceSession, Enrollment
from tests.helpers import PASSWORD, confirm, enroll, make_actor, open_session


def register(client, role, **extra):
    body = {"email": f"{role}-new@example.com", "password": PASSWORD, "first_name": "A", "last_name": "B", "role": role}
    return client.post("/api/v1/auth/register", json={**body, **extra})


def close(client, professor, session_id):
    response = client.post(f"/api/v1/attendance-sessions/{session_id}/close", headers=professor["headers"])
    assert response.status_code == 200, response.text


def stats(client, student):
    return client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()


def set_status(client, professor, session_id, student, status):
    return client.put(
        f"/api/v1/attendance-sessions/{session_id}/students/{student['id']}",
        json={"status": status},
        headers=professor["headers"],
    )


# --- Registration and sign-in -------------------------------------------------


def test_professor_registration_without_access_code_is_refused(client):
    assert register(client, "professor").status_code == 403


def test_professor_registration_with_wrong_access_code_is_refused(client):
    assert register(client, "professor", professor_code="guess").status_code == 403


def test_professor_registration_with_access_code_succeeds(client):
    assert register(client, "professor", professor_code="test-professor-code").status_code == 201


def test_student_registration_needs_no_code(client):
    assert register(client, "student").status_code == 201


def test_repeated_failed_logins_are_rate_limited(client, student):
    limit = get_settings().login_attempt_limit
    for _ in range(limit):
        response = client.post("/api/v1/auth/token", data={"username": "student@example.com", "password": "wrong-password"})
        assert response.status_code == 401
    blocked = client.post("/api/v1/auth/token", data={"username": "student@example.com", "password": PASSWORD})
    assert blocked.status_code == 429


def test_failed_logins_for_one_account_do_not_block_another(client, student, other_student):
    for _ in range(get_settings().login_attempt_limit):
        client.post("/api/v1/auth/token", data={"username": "student@example.com", "password": "wrong-password"})
    ok = client.post("/api/v1/auth/token", data={"username": "student2@example.com", "password": PASSWORD})
    assert ok.status_code == 200


def test_production_refuses_development_secrets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "development-jwt-secret-change-me")
    with pytest.raises(RuntimeError):
        check_production_settings()


def test_production_accepts_strong_distinct_secrets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "a" * 40)
    monkeypatch.setattr(settings, "code_secret", "b" * 40)
    check_production_settings()


# --- Typed codes ----------------------------------------------------------------


def test_code_is_accepted_with_lowercase_spaces_and_dashes(client, professor, student, section):
    session = open_session(client, professor, section)
    code = session["code"]
    typed = f"{code[:5].lower()} - {code[5:].lower()}"
    assert confirm(client, student, session["id"], code=typed).status_code == 200


# --- Late, excused, and manual corrections ---------------------------------------


def test_confirmation_after_late_threshold_is_late(client, professor, student, section, db):
    session = open_session(client, professor, section, late_after_minutes=5)
    row = db.get(AttendanceSession, session["id"])
    row.starts_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    db.commit()
    response = confirm(client, student, session["id"], code=session["code"])
    assert response.json()["status"] == "late"
    assert stats(client, student)["late"] == 1


def test_confirmation_before_late_threshold_is_present(client, professor, student, section):
    session = open_session(client, professor, section, late_after_minutes=5)
    assert confirm(client, student, session["id"], code=session["code"]).json()["status"] == "present"


def test_approved_absence_excuses_the_session(client, professor, student, section):
    session = open_session(client, professor, section)
    close(client, professor, session["id"])
    request = client.post(
        "/api/v1/absence-requests",
        json={"section_id": section, "attendance_session_id": session["id"], "reason": "Medical appointment"},
        headers=student["headers"],
    ).json()
    client.patch(f"/api/v1/absence-requests/{request['id']}", json={"status": "approved"}, headers=professor["headers"])
    result = stats(client, student)
    assert result["excused"] == 1 and result["absent"] == 0
    assert result["by_session"][0]["status"] == "excused"
    assert result["attendance_percentage"] == 0  # no non-excused sessions to attend


def test_excused_session_is_left_out_of_the_percentage(client, professor, student, section):
    attended = open_session(client, professor, section)
    confirm(client, student, attended["id"], code=attended["code"])
    close(client, professor, attended["id"])
    missed = open_session(client, professor, section)
    close(client, professor, missed["id"])
    set_status(client, professor, missed["id"], student, "excused")
    assert stats(client, student)["attendance_percentage"] == 100.0


def test_rejected_absence_leaves_student_absent(client, professor, student, section):
    session = open_session(client, professor, section)
    close(client, professor, session["id"])
    request = client.post(
        "/api/v1/absence-requests",
        json={"section_id": section, "attendance_session_id": session["id"], "reason": "Medical appointment"},
        headers=student["headers"],
    ).json()
    client.patch(f"/api/v1/absence-requests/{request['id']}", json={"status": "rejected"}, headers=professor["headers"])
    assert stats(client, student)["absent"] == 1


def test_excused_student_who_attends_is_recorded_present(client, professor, student, section):
    session = open_session(client, professor, section)
    set_status(client, professor, session["id"], student, "excused")
    response = confirm(client, student, session["id"], code=session["code"])
    assert response.status_code == 200
    assert response.json()["status"] == "present"


def test_professor_marks_student_present_by_hand(client, professor, student, section):
    session = open_session(client, professor, section)
    close(client, professor, session["id"])
    response = set_status(client, professor, session["id"], student, "present")
    assert response.status_code == 200
    assert response.json()["confirmation_method"] == "manual"
    assert stats(client, student)["present"] == 1
    notes = client.get("/api/v1/notifications", headers=student["headers"]).json()
    assert notes[0]["title"] == "Attendance updated"


def test_professor_can_undo_a_confirmation(client, professor, student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    close(client, professor, session["id"])
    assert set_status(client, professor, session["id"], student, "absent").json()["status"] == "absent"
    assert stats(client, student)["absent"] == 1


def test_manual_correction_rejects_unknown_status(client, professor, student, section):
    session = open_session(client, professor, section)
    assert set_status(client, professor, session["id"], student, "maybe").status_code == 422


def test_other_professor_cannot_correct_attendance(client, other_professor, student, professor, section):
    session = open_session(client, professor, section)
    assert set_status(client, other_professor, session["id"], student, "present").status_code == 404


def test_student_cannot_correct_attendance(client, professor, student, section):
    session = open_session(client, professor, section)
    assert set_status(client, student, session["id"], student, "present").status_code == 403


def test_cannot_mark_a_student_outside_the_class(client, professor, outsider, section):
    session = open_session(client, professor, section)
    assert set_status(client, professor, session["id"], outsider, "present").status_code == 404


def test_roster_shows_pending_then_absent(client, professor, student, other_student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    url = f"/api/v1/attendance-sessions/{session['id']}/roster"
    live = {row["student"]["id"]: row["status"] for row in client.get(url, headers=professor["headers"]).json()}
    assert live == {student["id"]: "present", other_student["id"]: "pending"}
    close(client, professor, session["id"])
    ended = {row["student"]["id"]: row["status"] for row in client.get(url, headers=professor["headers"]).json()}
    assert ended[other_student["id"]] == "absent"


# --- Enrollment history ------------------------------------------------------------


def test_sessions_before_enrollment_do_not_count(client, professor, section):
    earlier = open_session(client, professor, section)
    close(client, professor, earlier["id"])
    newcomer = make_actor(client, "late-joiner@example.com", "student")
    enroll(client, professor, section, newcomer)
    assert stats(client, newcomer)["total_sessions"] == 0
    class_stats = client.get(f"/api/v1/sections/{section}/statistics", headers=professor["headers"]).json()
    assert class_stats["by_session"][0]["enrolled"] == 0


def test_removed_student_keeps_history_but_leaves_the_class(client, professor, student, section):
    attended = open_session(client, professor, section)
    confirm(client, student, attended["id"], code=attended["code"])
    close(client, professor, attended["id"])
    removed = client.delete(f"/api/v1/sections/{section}/enrollments/{student['id']}", headers=professor["headers"])
    assert removed.status_code == 204
    later = open_session(client, professor, section)
    close(client, professor, later["id"])
    result = stats(client, student)
    assert result["total_sessions"] == 1 and result["present"] == 1
    assert client.get("/api/v1/sections", headers=student["headers"]).json() == []
    roster = client.get(f"/api/v1/sections/{section}/students", headers=professor["headers"]).json()
    assert roster == []


def test_removed_student_can_be_added_again(client, professor, student, section):
    client.delete(f"/api/v1/sections/{section}/enrollments/{student['id']}", headers=professor["headers"])
    again = client.post(
        f"/api/v1/sections/{section}/enrollments", json={"email": "student@example.com"}, headers=professor["headers"]
    )
    assert again.status_code == 201
    assert len(client.get("/api/v1/sections", headers=student["headers"]).json()) == 1


def test_removing_a_student_not_in_class_is_not_found(client, professor, outsider, section):
    response = client.delete(f"/api/v1/sections/{section}/enrollments/{outsider['id']}", headers=professor["headers"])
    assert response.status_code == 404


def test_legacy_enrollment_without_date_counts_from_the_start(client, professor, student, section, db):
    session = open_session(client, professor, section)
    close(client, professor, session["id"])
    enrollment = db.query(Enrollment).filter_by(student_id=student["id"]).one()
    enrollment.enrolled_at = None
    db.commit()
    assert stats(client, student)["absent"] == 1


def test_class_statistics_wait_for_open_session_to_end(client, professor, student, section):
    open_session(client, professor, section)
    class_stats = client.get(f"/api/v1/sections/{section}/statistics", headers=professor["headers"]).json()
    assert class_stats["total_sessions"] == 0


# --- Attachments ---------------------------------------------------------------------

PDF = b"%PDF-1.4\n%fake but well-formed enough for a signature check\n"


def make_request(client, student, section):
    return client.post(
        "/api/v1/absence-requests",
        json={"section_id": section, "reason": "Medical appointment"},
        headers=student["headers"],
    ).json()


def upload(client, actor, request_id, content, name="note.pdf"):
    return client.post(
        f"/api/v1/absence-requests/{request_id}/attachment",
        files={"file": (name, content, "application/pdf")},
        headers=actor["headers"],
    )


def test_student_uploads_pdf_and_professor_opens_it(client, professor, student, section):
    request = make_request(client, student, section)
    response = upload(client, student, request["id"], PDF)
    assert response.status_code == 200
    assert response.json()["has_attachment"] is True
    assert response.json()["attachment_name"] == "note.pdf"
    download = client.get(f"/api/v1/absence-requests/{request['id']}/attachment", headers=professor["headers"])
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["x-content-type-options"] == "nosniff"


def test_upload_rejects_files_that_are_not_pdf_or_image(client, student, section):
    request = make_request(client, student, section)
    response = upload(client, student, request["id"], b"<script>alert(1)</script>", name="note.pdf")
    assert response.status_code == 415


def test_upload_rejects_large_files(client, student, section, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 32)
    request = make_request(client, student, section)
    assert upload(client, student, request["id"], PDF + b"x" * 64).status_code == 413


def test_other_users_cannot_open_or_replace_the_attachment(client, other_professor, student, other_student, section):
    request = make_request(client, student, section)
    upload(client, student, request["id"], PDF)
    url = f"/api/v1/absence-requests/{request['id']}/attachment"
    assert client.get(url, headers=other_student["headers"]).status_code == 404
    assert client.get(url, headers=other_professor["headers"]).status_code == 404
    assert upload(client, other_student, request["id"], PDF).status_code == 404


def test_attachment_cannot_change_after_review(client, professor, student, section):
    request = make_request(client, student, section)
    client.patch(f"/api/v1/absence-requests/{request['id']}", json={"status": "rejected"}, headers=professor["headers"])
    assert upload(client, student, request["id"], PDF).status_code == 409


def test_attachment_url_must_be_http(client, student, section):
    response = client.post(
        "/api/v1/absence-requests",
        json={"section_id": section, "reason": "Medical appointment", "attachment_url": "javascript:alert(1)"},
        headers=student["headers"],
    )
    assert response.status_code == 422
