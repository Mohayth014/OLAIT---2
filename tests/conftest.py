import pytest
from fastapi.testclient import TestClient

from backend.database import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a throwaway SQLite file for the duration of a test."""
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "test_olai.db")
    db.init_db()
    return db


@pytest.fixture
def client(temp_db):
    from backend.app import app
    with TestClient(app) as c:
        yield c
