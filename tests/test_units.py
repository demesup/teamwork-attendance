import re
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.main import distance_meters
from app.models import as_utc
from app.security import digest_code, hash_password, make_code, make_qr_token, verify_password


def test_distance_zero_for_same_point():
    assert distance_meters(41.0, 28.9, 41.0, 28.9) == 0


def test_distance_one_thousandth_of_a_degree_latitude():
    # One degree of latitude is about 111.19 km, so 0.001 degrees is about 111.19 m.
    assert abs(distance_meters(41.0, 28.9, 41.001, 28.9) - 111.19) < 0.5


def test_distance_is_symmetric():
    forward = distance_meters(41.0, 28.9, 41.1, 29.0)
    backward = distance_meters(41.1, 29.0, 41.0, 28.9)
    assert abs(forward - backward) < 1e-6


def test_make_code_format():
    codes = [make_code() for _ in range(2000)]
    assert all(re.fullmatch(r"[A-Z0-9]{10}", code) for code in codes)
    assert len(set(codes)) == len(codes)


def test_make_code_avoids_look_alike_characters():
    codes = "".join(make_code() for _ in range(500))
    assert not set(codes) & set("0O1IL5S")


def test_make_code_has_no_lowercase_or_separators():
    codes = "".join(make_code() for _ in range(200))
    assert "-" not in codes and "_" not in codes
    assert codes == codes.upper()


def test_digest_is_deterministic_and_keyed():
    assert digest_code("ABC123") == digest_code("ABC123")
    assert digest_code("ABC123") != digest_code("ABC124")


def test_password_hash_verifies_and_rejects_wrong_value():
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-password-value", hashed)


def test_as_utc_treats_naive_datetime_as_utc():
    naive = datetime(2030, 1, 1, 12, 0)
    aware = as_utc(naive)
    assert aware.tzinfo is timezone.utc
    assert aware.hour == 12


def test_as_utc_keeps_aware_datetime_unchanged():
    value = datetime(2030, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    assert as_utc(value) == value


def test_qr_expiry_is_capped_by_session_expiry():
    session_end = datetime.now(timezone.utc) + timedelta(seconds=5)
    _, expires = make_qr_token("session-id", 1, session_end)
    assert expires == session_end


def test_qr_expiry_uses_ttl_when_session_is_longer():
    session_end = datetime.now(timezone.utc) + timedelta(hours=1)
    _, expires = make_qr_token("session-id", 1, session_end)
    remaining = expires - datetime.now(timezone.utc)
    assert timedelta(seconds=get_settings().qr_ttl_seconds - 2) < remaining <= timedelta(seconds=get_settings().qr_ttl_seconds)
