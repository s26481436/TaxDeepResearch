from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from taxwatch.config import get_settings
from taxwatch.models import Base

logger = logging.getLogger(__name__)

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
    _add_missing_columns(engine)
    _db_initialized = True


def _add_missing_columns(engine) -> None:
    """Add nullable columns the models gained since a database was created.

    `create_all` creates missing *tables* but never alters existing ones, and
    the project carries no migration tool. Without this, upgrading against a
    populated database fails at the first query naming a new column — with the
    only remedy being to drop the crawl history and start over.

    Deliberately narrow: nullable adds only, which every supported backend
    accepts without a table rewrite. Anything else (drops, type changes,
    constraints) needs a real migration and should not be smuggled in here.
    """
    inspector = inspect(engine)

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or not column.nullable or column.primary_key:
                continue
            ddl = (
                f'ALTER TABLE "{table.name}" '
                f'ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
            )
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info("Added missing column %s.%s", table.name, column.name)
