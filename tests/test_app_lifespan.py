"""Every FastAPI entry point must initialise the database before serving.

`taxwatch.api.main` once shipped the full router set without the startup hook
`taxwatch.web.app` had, so every endpoint on it failed with
`relation "tax_requirements" does not exist`. Sharing routers is not enough —
the lifecycle has to be shared too, and that has to stay true for entry points
added later.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import taxwatch.db as db
from taxwatch.api.main import app as api_app
from taxwatch.models import Base, TaxRequirement
from taxwatch.web.app import app as web_app

# Both entry points, so a third one added without a lifespan fails here.
APPS = pytest.mark.parametrize(
    "app",
    [pytest.param(api_app, id="json-api"), pytest.param(web_app, id="dashboard")],
)


@pytest.fixture
def empty_database(tmp_path, monkeypatch):
    """Point the app at a database with no tables at all."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    factory = sessionmaker(bind=engine)

    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_session_factory", factory)
    # A fresh process has ensured nothing yet.
    monkeypatch.setattr(db, "_initialized_tables", frozenset())
    return engine


@APPS
def test_startup_creates_the_schema(app, empty_database):
    from sqlalchemy import inspect

    assert not inspect(empty_database).get_table_names()

    with TestClient(app):
        pass

    created = set(inspect(empty_database).get_table_names())
    assert "tax_requirements" in created
    assert "requirement_fields" in created


@APPS
def test_endpoints_work_against_a_database_never_initialised(app, empty_database):
    with TestClient(app) as client:
        assert client.get("/api/requirements").status_code == 200
        assert client.get("/api/requirements/review").status_code == 200
        # 404 means "no such row" — the table itself resolved.
        assert client.get("/api/requirements/3").status_code == 404


class TestInitLatch:
    """The in-process latch must not mask a table the models gained later."""

    def test_skips_repeated_work(self, empty_database, monkeypatch):
        calls = []
        real = Base.metadata.create_all
        monkeypatch.setattr(
            Base.metadata, "create_all", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        )

        db.init_db()
        db.init_db()

        assert len(calls) == 1

    def test_reruns_when_the_models_gain_a_table(self, empty_database, monkeypatch):
        """A `--reload` process re-imports models in place; a bool latch would
        return early and leave the new table missing forever."""
        from sqlalchemy import inspect

        db.init_db()
        assert "tax_requirements" in inspect(empty_database).get_table_names()

        # Pretend this process initialised before TaxRequirement was defined.
        without_new_table = frozenset(db._initialized_tables) - {TaxRequirement.__tablename__}
        monkeypatch.setattr(db, "_initialized_tables", without_new_table)

        calls = []
        real = Base.metadata.create_all
        monkeypatch.setattr(
            Base.metadata, "create_all", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        )
        db.init_db()

        assert calls, "init_db short-circuited despite a table it had never ensured"
