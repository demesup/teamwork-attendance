"""Shared helpers for building users, sections, and attendance sessions in tests."""

PASSWORD = "correct-horse-battery"
PROFESSOR_CODE = "test-professor-code"


def make_actor(client, email: str, role: str) -> dict:
    """Register a user, log in, and return {"id": ..., "headers": {...}}."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Test",
            "last_name": role.title(),
            "role": role,
            "professor_code": PROFESSOR_CODE if role == "professor" else None,
        },
    )
    assert response.status_code == 201, response.text
    token = client.post("/api/v1/auth/token", data={"username": email, "password": PASSWORD})
    assert token.status_code == 200, token.text
    return {"id": response.json()["id"], "headers": {"Authorization": f"Bearer {token.json()['access_token']}"}}


def create_section(client, professor: dict, name: str = "Databases") -> str:
    response = client.post("/api/v1/sections", json={"name": name}, headers=professor["headers"])
    assert response.status_code == 201, response.text
    return response.json()["id"]


def enroll(client, professor: dict, section_id: str, student: dict) -> None:
    response = client.post(
        f"/api/v1/sections/{section_id}/enrollments",
        json={"student_id": student["id"]},
        headers=professor["headers"],
    )
    assert response.status_code == 201, response.text


def open_session(client, professor: dict, section_id: str, **extra) -> dict:
    response = client.post(
        "/api/v1/attendance-sessions",
        json={"section_id": section_id, **extra},
        headers=professor["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, student: dict, session_id: str, device: str | None = None, **body):
    headers = dict(student["headers"])
    if device:
        headers["X-Device-Id"] = device
    return client.post(f"/api/v1/attendance-sessions/{session_id}/confirm", json=body, headers=headers)
