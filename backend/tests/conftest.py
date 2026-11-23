from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="prism-test-"))
os.environ.update({
    "PRISM_ENV": "test",
    "PRISM_DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "PRISM_DATA_DIR": str(_TMP / "data"),
    "PRISM_JOBS_MODE": "inline",
    "PRISM_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
    "PRISM_AUTH_RATE_PER_MINUTE": "1000",
})
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.models import Base, Membership, User, Workspace  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402

PASSWORD = "correct-horse-battery"


def pytest_sessionfinish(session, exitstatus):
    get_engine().dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def seeded():
    Base.metadata.create_all(get_engine())
    with get_sessionmaker()() as db:
        ws = Workspace(name="Head Office", slug="ho")
        other = Workspace(name="Other Company", slug="other")
        db.add_all([ws, other])
        db.flush()
        users = {}
        for key, role, wsx in (("admin", "admin", ws), ("editor", "editor", ws), ("viewer", "viewer", ws), ("outsider", "admin", other)):
            u = User(email=f"{key}@prism.test", name=key.title(), password_hash=hash_password(PASSWORD))
            db.add(u)
            db.flush()
            db.add(Membership(user_id=u.id, workspace_id=wsx.id, role=role))
            users[key] = u.id
        db.commit()
        return {"ws": ws.id, "other_ws": other.id, "users": users}


@pytest.fixture(scope="session")
def client(seeded):
    with TestClient(create_app()) as c:
        yield c


def _login(client, who: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": f"{who}@prism.test", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="session")
def auth(client):
    return {who: _login(client, who) for who in ("admin", "editor", "viewer", "outsider")}
