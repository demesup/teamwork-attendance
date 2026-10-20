import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SEED_DEMO_DATA"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import DEMO_PASSWORD, PROFESSOR, STUDENTS  # noqa: E402


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/token", data={"username": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_full_attendance_flow() -> None:
    with TestClient(app) as client:
        professor = login(client, PROFESSOR[0])
        student = login(client, STUDENTS[0][0])

        assert client.get("/api/v1/auth/me", headers=student).json()["role"] == "student"

        sections = client.get("/api/v1/sections", headers=professor).json()
        assert len(sections) == 3
        section = next(s for s in sections if s["name"] == "Digital Electronics")
        assert section["enrolled_students"] == len(STUDENTS)

        opened = client.post(
            "/api/v1/attendance-sessions",
            json={"section_id": section["id"], "title": "Flip-Flops", "duration_minutes": 15},
            headers=professor,
        ).json()
        assert len(opened["code"]) == 6

        open_sessions = client.get("/api/v1/attendance-sessions?open=true", headers=student).json()
        assert [s["id"] for s in open_sessions] == [opened["id"]]

        assert client.post("/api/v1/attendance/confirm", json={"code": "ZZZZZZ"}, headers=student).status_code == 400

        rotated = client.post(f"/api/v1/attendance-sessions/{opened['id']}/rotate-code", headers=professor).json()
        stale = client.post("/api/v1/attendance/confirm", json={"code": opened["code"]}, headers=student)
        assert stale.status_code == 400

        confirmed = client.post(
            "/api/v1/attendance/confirm", json={"code": rotated["code"].lower()}, headers=student
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["section_name"] == "Digital Electronics"
        again = client.post("/api/v1/attendance/confirm", json={"code": rotated["code"]}, headers=student)
        assert again.status_code == 409

        records = client.get(f"/api/v1/attendance-sessions/{opened['id']}/records", headers=professor).json()
        assert [r["email"] for r in records] == [STUDENTS[0][0]]

        assert client.post(f"/api/v1/attendance-sessions/{opened['id']}/close", headers=professor).status_code == 200
        assert client.get("/api/v1/attendance-sessions?open=true", headers=student).json() == []

        stats = client.get("/api/v1/students/me/statistics", headers=student).json()
        assert stats["by_session"][0]["title"] == "Flip-Flops"
        assert stats["by_session"][0]["status"] == "present"


def test_enroll_by_email() -> None:
    with TestClient(app) as client:
        professor = login(client, PROFESSOR[0])
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "new.student@student.university.edu",
                "password": "another-password-1",
                "first_name": "New",
                "last_name": "Student",
                "role": "student",
            },
        )
        section = client.post("/api/v1/sections", json={"name": "Robotics"}, headers=professor).json()
        enrolled = client.post(
            f"/api/v1/sections/{section['id']}/enrollments",
            json={"student_email": "new.student@student.university.edu"},
            headers=professor,
        )
        assert enrolled.status_code == 201, enrolled.text
        students = client.get(f"/api/v1/sections/{section['id']}/students", headers=professor).json()
        assert [s["first_name"] for s in students] == ["New"]
