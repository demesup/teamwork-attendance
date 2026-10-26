import os
import tempfile
from pathlib import Path

# Must run before `app` is imported: settings are read at import time.
_test_dir = Path(tempfile.mkdtemp(prefix="attendance-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_test_dir.as_posix()}/test.db"
os.environ["JWT_SECRET"] = "test-jwt-secret-0123456789abcdef0123456789"
os.environ["CODE_SECRET"] = "test-code-secret-0123456789abcdef012345678"
os.environ["PROFESSOR_SIGNUP_CODE"] = "test-professor-code"
os.environ["UPLOAD_DIR"] = f"{_test_dir.as_posix()}/uploads"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine, ensure_additive_schema  # noqa: E402
from app.main import app  # noqa: E402
from tests.helpers import create_section, enroll, make_actor  # noqa: E402


@pytest.fixture
def client():
    """A fresh database for every test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def professor(client):
    return make_actor(client, "prof@example.com", "professor")


@pytest.fixture
def other_professor(client):
    return make_actor(client, "other-prof@example.com", "professor")


@pytest.fixture
def section(client, professor):
    return create_section(client, professor)


@pytest.fixture
def student(client, professor, section):
    """An enrolled student."""
    actor = make_actor(client, "student@example.com", "student")
    enroll(client, professor, section, actor)
    return actor


@pytest.fixture
def other_student(client, professor, section):
    """A second enrolled student."""
    actor = make_actor(client, "student2@example.com", "student")
    enroll(client, professor, section, actor)
    return actor


@pytest.fixture
def outsider(client):
    """A student who is not enrolled in the section."""
    return make_actor(client, "outsider@example.com", "student")
