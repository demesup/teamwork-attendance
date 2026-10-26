"""Tests for the anti-sharing controls: QR nonces, code rotation, rate limits, and device/IP checks."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import get_settings
from app.models import AttendanceRecord, AttendanceSession, AuditLog, CodeAttempt
from tests.helpers import confirm, enroll, make_actor, open_session


# ---------- One-time QR tokens ----------


def test_qr_token_confirms_attendance_once(client, professor, section, student):
    body = open_session(client, professor, section)
    response = confirm(client, student, body["id"], qr_token=body["qr_token"])
    assert response.status_code == 200
    assert response.json()["confirmation_method"] == "qr"


def test_same_qr_token_cannot_be_used_by_a_second_student(client, professor, section, student, other_student):
    body = open_session(client, professor, section)
    assert confirm(client, student, body["id"], qr_token=body["qr_token"]).status_code == 200
    response = confirm(client, other_student, body["id"], qr_token=body["qr_token"])
    assert response.status_code == 409
    assert response.json()["detail"] == "QR code has already been used"


def test_refreshed_qr_token_works_for_each_student(client, professor, section, student, other_student):
    body = open_session(client, professor, section)
    first = client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"]).json()
    second = client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"]).json()
    assert first["qr_token"] != second["qr_token"]
    assert confirm(client, student, body["id"], qr_token=first["qr_token"]).status_code == 200
    assert confirm(client, other_student, body["id"], qr_token=second["qr_token"]).status_code == 200


def test_qr_token_nonces_are_unique_per_issue(client, professor, section):
    body = open_session(client, professor, section)
    tokens = [
        client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"]).json()["qr_token"]
        for _ in range(5)
    ]
    nonces = {jwt.decode(token, get_settings().code_secret, algorithms=["HS256"])["nonce"] for token in tokens}
    assert len(nonces) == 5


def test_qr_token_expires_after_ttl(client, professor, section, student):
    body = open_session(client, professor, section)
    expired = jwt.encode(
        {
            "session_id": body["id"],
            "code_version": 1,
            "nonce": "expired-nonce",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        get_settings().code_secret,
        algorithm="HS256",
    )
    response = confirm(client, student, body["id"], qr_token=expired)
    assert response.status_code == 400


def test_qr_token_lifetime_is_capped_at_ttl(client, professor, section):
    body = open_session(client, professor, section)
    issued = client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"]).json()
    expires = datetime.fromisoformat(issued["expires_at"])
    remaining = expires - datetime.now(timezone.utc)
    assert remaining <= timedelta(seconds=get_settings().qr_ttl_seconds, milliseconds=500)


def test_qr_token_from_another_session_is_rejected(client, professor, section, student):
    first = open_session(client, professor, section)
    second = open_session(client, professor, section)
    response = confirm(client, student, second["id"], qr_token=first["qr_token"])
    assert response.status_code == 400


def test_qr_token_with_forged_signature_is_rejected(client, professor, section, student):
    body = open_session(client, professor, section)
    forged = jwt.encode(
        {"session_id": body["id"], "code_version": 1, "nonce": "forged", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "not-the-real-secret-0123456789abcdef012345",
        algorithm="HS256",
    )
    assert confirm(client, student, body["id"], qr_token=forged).status_code == 400


def test_qr_token_without_nonce_is_rejected(client, professor, section, student):
    body = open_session(client, professor, section)
    no_nonce = jwt.encode(
        {"session_id": body["id"], "code_version": 1, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        get_settings().code_secret,
        algorithm="HS256",
    )
    assert confirm(client, student, body["id"], qr_token=no_nonce).status_code == 400


def test_qr_token_only_issued_to_owner_and_open_sessions(client, professor, other_professor, section, db):
    body = open_session(client, professor, section)
    assert client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=other_professor["headers"]).status_code == 404

    client.post(f"/api/v1/attendance-sessions/{body['id']}/cancel", headers=professor["headers"])
    assert client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"]).status_code == 409


def test_qr_token_refresh_on_expired_session_returns_410(client, professor, section, db):
    body = open_session(client, professor, section)
    session = db.get(AttendanceSession, body["id"])
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    response = client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=professor["headers"])
    assert response.status_code == 410


def test_qr_nonce_is_stored_on_the_record(client, professor, section, student, db):
    body = open_session(client, professor, section)
    confirm(client, student, body["id"], qr_token=body["qr_token"])
    record = db.query(AttendanceRecord).filter_by(student_id=student["id"]).one()
    assert record.qr_nonce


# ---------- Code rotation ----------


def test_rotated_code_replaces_the_old_one(client, professor, section, student, other_student):
    body = open_session(client, professor, section)
    rotated = client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=professor["headers"])
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["code"] != body["code"]
    assert new["code_version"] == 2

    assert confirm(client, student, body["id"], code=body["code"]).status_code == 400
    assert confirm(client, other_student, body["id"], code=new["code"]).status_code == 200


def test_rotation_invalidates_qr_tokens_issued_before(client, professor, section, student):
    body = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=professor["headers"])
    assert confirm(client, student, body["id"], qr_token=body["qr_token"]).status_code == 400


def test_rotation_is_audited(client, professor, section, db):
    body = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=professor["headers"])
    actions = [row.action for row in db.query(AuditLog).all()]
    assert "attendance_code_rotated" in actions


def test_rotation_only_by_owner(client, professor, other_professor, section):
    body = open_session(client, professor, section)
    response = client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=other_professor["headers"])
    assert response.status_code == 404


def test_rotation_on_closed_session_is_a_conflict(client, professor, section):
    body = open_session(client, professor, section)
    client.post(f"/api/v1/attendance-sessions/{body['id']}/cancel", headers=professor["headers"])
    response = client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=professor["headers"])
    assert response.status_code == 409


def test_students_cannot_rotate_or_refresh(client, professor, section, student):
    body = open_session(client, professor, section)
    assert client.post(f"/api/v1/attendance-sessions/{body['id']}/rotate-code", headers=student["headers"]).status_code == 403
    assert client.post(f"/api/v1/attendance-sessions/{body['id']}/qr-token", headers=student["headers"]).status_code == 403


# ---------- Rate limiting ----------


def test_student_is_locked_out_after_repeated_failures(client, professor, section, student):
    body = open_session(client, professor, section)
    limit = get_settings().attempt_limit_per_student
    for _ in range(limit):
        assert confirm(client, student, body["id"], code="WRONGCODE12").status_code == 400

    locked = confirm(client, student, body["id"], code=body["code"])
    assert locked.status_code == 429


def test_lockout_is_logged_as_rate_limited(client, professor, section, student, db):
    body = open_session(client, professor, section)
    for _ in range(get_settings().attempt_limit_per_student):
        confirm(client, student, body["id"], code="WRONGCODE12")
    confirm(client, student, body["id"], code="WRONGCODE12")
    assert db.query(CodeAttempt).filter_by(result="rate_limited").count() == 1


def test_lockout_is_per_session(client, professor, section, student):
    first = open_session(client, professor, section)
    second = open_session(client, professor, section)
    for _ in range(get_settings().attempt_limit_per_student):
        confirm(client, student, first["id"], code="WRONGCODE12")
    assert confirm(client, student, first["id"], code=first["code"]).status_code == 429
    assert confirm(client, student, second["id"], code=second["code"]).status_code == 200


def test_failures_below_the_limit_do_not_lock_out(client, professor, section, student):
    body = open_session(client, professor, section)
    for _ in range(get_settings().attempt_limit_per_student - 1):
        confirm(client, student, body["id"], code="WRONGCODE12")
    assert confirm(client, student, body["id"], code=body["code"]).status_code == 200


def test_ip_limit_blocks_many_students_from_one_address(client, professor, section, student, other_student, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "attempt_limit_per_ip", 2)
    body = open_session(client, professor, section)
    third = make_actor(client, "third@example.com", "student")
    enroll(client, professor, section, third)

    assert confirm(client, student, body["id"], code="WRONGCODE12").status_code == 400
    assert confirm(client, other_student, body["id"], code="WRONGCODE12").status_code == 400
    assert confirm(client, third, body["id"], code=body["code"]).status_code == 429


def test_device_limit_blocks_rotating_accounts_on_one_device(client, professor, section, student, other_student, monkeypatch):
    monkeypatch.setattr(get_settings(), "attempt_limit_per_device", 2)
    body = open_session(client, professor, section)
    assert confirm(client, student, body["id"], device="dev-1", code="WRONGCODE12").status_code == 400
    assert confirm(client, other_student, body["id"], device="dev-1", code="WRONGCODE12").status_code == 400
    assert confirm(client, other_student, body["id"], device="dev-1", code=body["code"]).status_code == 429


def test_lockout_window_expires(client, professor, section, student, db):
    body = open_session(client, professor, section)
    for _ in range(get_settings().attempt_limit_per_student):
        confirm(client, student, body["id"], code="WRONGCODE12")
    # Move every recorded attempt outside the window.
    old = datetime.now(timezone.utc) - timedelta(minutes=get_settings().attempt_window_minutes + 1)
    for row in db.query(CodeAttempt).all():
        row.attempted_at = old
    db.commit()
    assert confirm(client, student, body["id"], code=body["code"]).status_code == 200


# ---------- Device and IP recording ----------


def test_device_conflict_blocks_second_student_on_same_device(client, professor, section, student, other_student):
    body = open_session(client, professor, section)
    assert confirm(client, student, body["id"], device="phone-A", code=body["code"]).status_code == 200
    response = confirm(client, other_student, body["id"], device="phone-A", code=body["code"])
    assert response.status_code == 409
    assert "device" in response.json()["detail"].lower()


def test_same_device_can_be_used_by_different_session(client, professor, section, student, other_student):
    first = open_session(client, professor, section)
    second = open_session(client, professor, section)
    assert confirm(client, student, first["id"], device="phone-A", code=first["code"]).status_code == 200
    assert confirm(client, other_student, second["id"], device="phone-A", code=second["code"]).status_code == 200


def test_device_id_over_128_chars_is_rejected(client, professor, section, student):
    body = open_session(client, professor, section)
    response = confirm(client, student, body["id"], device="x" * 129, code=body["code"])
    assert response.status_code == 422


def test_record_and_attempts_store_ip_and_device(client, professor, section, student, db):
    body = open_session(client, professor, section)
    confirm(client, student, body["id"], device="phone-A", code=body["code"])
    record = db.query(AttendanceRecord).filter_by(student_id=student["id"]).one()
    assert record.device_id == "phone-A"
    assert record.ip_address  # TestClient reports a fixed host; any non-empty value is fine
    attempt = db.query(CodeAttempt).filter_by(student_id=student["id"], result="accepted").one()
    assert attempt.device_id == "phone-A"
    assert attempt.ip_address == record.ip_address


def test_duplicate_confirmation_from_same_student_and_device_is_conflict(client, professor, section, student):
    body = open_session(client, professor, section)
    assert confirm(client, student, body["id"], device="phone-A", code=body["code"]).status_code == 200
    assert confirm(client, student, body["id"], device="phone-A", code=body["code"]).status_code == 409


@pytest.mark.parametrize("bad_code", ["", "   "])
def test_blank_or_too_short_code_fails_validation(client, professor, section, student, bad_code):
    body = open_session(client, professor, section)
    response = confirm(client, student, body["id"], code=bad_code)
    assert response.status_code == 422
