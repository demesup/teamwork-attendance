from app.models import User
from tests.helpers import PASSWORD, PROFESSOR_CODE


def register(client, email="new@example.com", role="student", password=PASSWORD, professor_code=PROFESSOR_CODE):
    return client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "A",
            "last_name": "B",
            "role": role,
            "professor_code": professor_code,
        },
    )


def login(client, email, password=PASSWORD):
    return client.post("/api/v1/auth/token", data={"username": email, "password": password})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_register_creates_user_and_lowercases_email(client):
    response = register(client, email="Mixed.Case@Example.com", role="professor")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "mixed.case@example.com"
    assert body["role"] == "professor"
    assert "password" not in body and "password_hash" not in body


def test_register_rejects_duplicate_email_regardless_of_case(client):
    assert register(client, email="dup@example.com").status_code == 201
    response = register(client, email="DUP@example.com")
    assert response.status_code == 409


def test_register_rejects_password_shorter_than_12(client):
    assert register(client, password="short").status_code == 422


def test_register_rejects_unknown_role(client):
    assert register(client, role="admin").status_code == 422


def test_password_is_stored_as_argon2_hash_not_plaintext(client, db):
    register(client, email="hash@example.com")
    user = db.query(User).filter_by(email="hash@example.com").one()
    assert user.password_hash != PASSWORD
    assert user.password_hash.startswith("$argon2")


def test_login_returns_bearer_token(client):
    register(client, email="login@example.com")
    response = login(client, "login@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password_is_rejected(client):
    register(client, email="wrong@example.com")
    assert login(client, "wrong@example.com", password="not-the-password-at-all").status_code == 401


def test_login_unknown_email_is_rejected_with_same_message(client):
    response = login(client, "nobody@example.com")
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_protected_endpoint_requires_token(client):
    assert client.get("/api/v1/notifications").status_code == 401


def test_protected_endpoint_rejects_garbage_token(client):
    response = client.get("/api/v1/notifications", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_token_signed_with_other_secret_is_rejected(client, student):
    import jwt

    forged = jwt.encode({"sub": student["id"], "role": "professor"}, "wrong-secret-0123456789abcdef0123456789ab", algorithm="HS256")
    response = client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_deactivated_user_is_rejected_even_with_valid_token(client, student, db):
    assert client.get("/api/v1/notifications", headers=student["headers"]).status_code == 200
    user = db.get(User, student["id"])
    user.is_active = False
    db.commit()
    assert client.get("/api/v1/notifications", headers=student["headers"]).status_code == 401


def test_role_is_enforced_on_professor_endpoints(client, student):
    response = client.post("/api/v1/sections", json={"name": "X"}, headers=student["headers"])
    assert response.status_code == 403
