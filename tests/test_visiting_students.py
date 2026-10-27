from tests.helpers import create_section


def test_professor_sees_every_student_in_their_classes_with_their_classes(client, professor, section, student):
    other = create_section(client, professor, "Lab B")
    response = client.get("/api/v1/students", headers=professor["headers"])
    assert response.status_code == 200, response.text
    listed = {person["id"]: person for person in response.json()}
    assert listed[student["id"]]["classes"] == ["Databases"]
    assert other not in listed  # an empty class adds nobody


def test_a_student_from_another_group_can_be_recorded_at_a_lab(client, professor, section, student):
    lab = create_section(client, professor, "Lab B")
    response = client.post(
        f"/api/v1/sections/{lab}/attendance-scans/confirm",
        json={"session_date": "2026-10-26", "sessions": [{"label": "S1", "student_ids": [student["id"]]}]},
        headers=professor["headers"],
    )
    assert response.status_code == 201, response.text
    stats = client.get("/api/v1/students/me/statistics", headers=student["headers"]).json()
    assert [item["section_name"] for item in stats["by_session"]] == ["Lab B"]
    assert stats["by_session"][0]["status"] == "present"


def test_students_outside_the_professors_classes_are_refused(client, professor, section, outsider):
    response = client.post(
        f"/api/v1/sections/{section}/attendance-scans/confirm",
        json={"session_date": "2026-10-26", "sessions": [{"label": "S1", "student_ids": [outsider["id"]]}]},
        headers=professor["headers"],
    )
    assert response.status_code == 422
