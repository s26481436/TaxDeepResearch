from __future__ import annotations

import taxwatch.models as _models_module
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from taxwatch.config import get_settings

_engine = None
_session_factory: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, echo=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory


def get_session() -> Session:
    return get_session_factory()()


def init_db():
    """Create schema (if configured) and all tables."""
    settings = get_settings()
    schema = settings.db_schema.strip() or None

    # Re-build Base with the correct schema so all Table definitions pick it up.
    _models_module.Base = _models_module._make_base(schema)

    engine = get_engine()

    if schema:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.commit()

    _models_module.Base.metadata.create_all(engine)
