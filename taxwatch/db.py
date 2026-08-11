from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from taxwatch.config import get_settings
from taxwatch.models import Base

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


_db_initialized = False


def init_db():
    """Create schema (if configured) and all tables.

    Safe to call multiple times — skips work after the first successful call.
    """
    global _db_initialized
    if _db_initialized:
        return

    settings = get_settings()
    schema = settings.db_schema.strip() or None

    engine = get_engine()

    if schema:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(text(f'SET search_path TO "{schema}", public'))
            conn.commit()
        # Set search_path for all future connections from this engine.
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def set_search_path(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute(f'SET search_path TO "{schema}", public')
            cursor.close()

    Base.metadata.create_all(engine)
    _db_initialized = True
