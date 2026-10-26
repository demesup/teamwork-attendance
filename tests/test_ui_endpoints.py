"""Endpoints that let the web UI discover sections, sessions, and people without knowing ids up front."""

from tests.helpers import PASSWORD, confirm, make_actor, open_session


def test_me_returns_the_signed_in_user(client, professor):
    body = client.get("/api/v1/users/me", headers=professor["headers"]).json()
    assert body["id"] == professor["id"]
    assert body["role"] == "professor"


def test_browser_login_sets_http_only_cookie_that_authenticates(client, student):
    response = client.post("/api/v1/auth/login", data={"username": "student@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert "access_token" not in response.json()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/v1/users/me").json()["id"] == student["id"]


def test_logout_clears_the_cookie(client, student):
    client.post("/api/v1/auth/login", data={"username": "student@example.com", "password": PASSWORD})
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/users/me").status_code == 401


def test_browser_login_rejects_wrong_password(client, student):
    response = client.post("/api/v1/auth/login", data={"username": "student@example.com", "password": "wrong-password"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_enroll_by_email(client, professor, section, outsider):
    response = client.post(
        f"/api/v1/sections/{section}/enrollments",
        json={"email": "OUTSIDER@example.com"},
        headers=professor["headers"],
    )
    assert response.status_code == 201
    assert response.json()["student"]["id"] == outsider["id"]


def test_enroll_by_unknown_email_is_not_found(client, professor, section):
    response = client.post(
        f"/api/v1/sections/{section}/enrollments",
        json={"email": "nobody@example.com"},
        headers=professor["headers"],
    )
    assert response.status_code == 404


def test_enroll_requires_an_identifier(client, professor, section):
    response = client.post(f"/api/v1/sections/{section}/enrollments", json={}, headers=professor["headers"])
    assert response.status_code == 422


def test_professor_lists_only_own_sections(client, professor, other_professor, section):
    mine = client.get("/api/v1/sections", headers=professor["headers"]).json()
    theirs = client.get("/api/v1/sections", headers=other_professor["headers"]).json()
    assert [s["id"] for s in mine] == [section]
    assert theirs == []


def test_student_sees_enrolled_sections_with_open_session(client, professor, student, outsider, section):
    session = open_session(client, professor, section)
    sections = client.get("/api/v1/sections", headers=student["headers"]).json()
    assert len(sections) == 1
    assert sections[0]["open_session"]["id"] == session["id"]
    assert sections[0]["open_session"]["already_confirmed"] is False
    assert sections[0]["student_count"] == 1
    assert client.get("/api/v1/sections", headers=outsider["headers"]).json() == []


def test_open_session_shows_as_confirmed_after_confirming(client, professor, student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    sections = client.get("/api/v1/sections", headers=student["headers"]).json()
    assert sections[0]["open_session"]["already_confirmed"] is True


def test_section_students_lists_enrolled_students(client, professor, student, section):
    body = client.get(f"/api/v1/sections/{section}/students", headers=professor["headers"]).json()
    assert [s["email"] for s in body] == ["student@example.com"]


def test_section_students_hidden_from_other_professor(client, other_professor, section):
    response = client.get(f"/api/v1/sections/{section}/students", headers=other_professor["headers"])
    assert response.status_code == 404


def test_section_sessions_report_status_and_count(client, professor, student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    body = client.get(f"/api/v1/sections/{section}/sessions", headers=professor["headers"]).json()
    assert body[0]["id"] == session["id"]
    assert body[0]["status"] == "open"
    assert body[0]["confirmed"] == 1


def test_session_records_show_who_confirmed(client, professor, student, section):
    session = open_session(client, professor, section)
    confirm(client, student, session["id"], code=session["code"])
    body = client.get(f"/api/v1/attendance-sessions/{session['id']}/records", headers=professor["headers"]).json()
    assert body[0]["student"]["email"] == "student@example.com"
    assert body[0]["confirmation_method"] == "code"


def test_session_records_hidden_from_students(client, professor, student, section):
    session = open_session(client, professor, section)
    response = client.get(f"/api/v1/attendance-sessions/{session['id']}/records", headers=student["headers"])
    assert response.status_code == 403


def test_close_session_stops_confirmations_but_still_counts(client, professor, student, section):
    session = open_session(client, professor, section)
    assert client.post(f"/api/v1/attendance-sessions/{session['id']}/close", headers=professor["headers"]).status_code == 200
    assert confirm(client, student, session["id"], code=session["code"]).status_code == 404
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["total_sessions"] == 1 and stats["absent"] == 1
    again = client.post(f"/api/v1/attendance-sessions/{session['id']}/close", headers=professor["headers"])
    assert again.status_code == 409


def test_student_lists_own_absence_requests_with_names(client, professor, student, other_student, section):
    for actor in (student, other_student):
        client.post(
            "/api/v1/absence-requests",
            json={"section_id": section, "reason": "Medical appointment"},
            headers=actor["headers"],
        )
    body = client.get("/api/v1/absence-requests/me", headers=student["headers"]).json()
    assert len(body) == 1
    assert body[0]["student_id"] == student["id"]
    assert body[0]["section_name"] == "Databases"
    assert body[0]["student_name"] == "Test Student"


def test_mark_notifications_read(client, professor, student, section):
    session = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{session['id']}/cancel", headers=professor["headers"])
    assert client.post("/api/v1/notifications/read", headers=student["headers"]).status_code == 204
    notes = client.get("/api/v1/notifications", headers=student["headers"]).json()
    assert notes and all(note["read_at"] for note in notes)


def test_web_ui_is_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_unknown_api_path_is_not_served_the_ui(client):
    assert client.get("/api/v1/does-not-exist").status_code == 404


def test_timestamps_are_serialized_with_utc_offset(client, professor, student, section):
    """SQLite drops the timezone; without an explicit offset browsers read the time as local."""
    open_session(client, professor, section)
    open_summary = client.get("/api/v1/sections", headers=student["headers"]).json()[0]["open_session"]
    assert open_summary["expires_at"].endswith(("Z", "+00:00"))


def test_open_unconfirmed_session_is_not_yet_counted_as_absent(client, professor, student, section):
    open_session(client, professor, section)
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert stats["total_sessions"] == 0 and stats["absent"] == 0
