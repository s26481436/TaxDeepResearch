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
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            echo=False,
            **_schema_connect_args(settings.database_url, settings.db_schema.strip()),
        )
    return _engine


def _schema_connect_args(url: str, schema: str) -> dict:
    """Put the configured schema on the connection string itself.

    Setting `search_path` from a `connect` event listener looks equivalent but
    runs too late: SQLAlchemy resolves `dialect.default_schema_name` while
    establishing the very first connection, so it records `public` and every
    later `get_table_names()` reads the wrong schema — reporting a fully
    populated database as empty. Passing it as a libpq option applies it during
    connection setup, before the dialect looks.

    Naming a schema that does not exist yet is legal in PostgreSQL — it is
    ignored until created — so this is safe ahead of `CREATE SCHEMA`.

    The name is quoted as an identifier and then escaped for libpq. Dropping
    either step bites on real schema names: `fin-tax` survives unquoted by
    luck, but a name containing a space splits the libpq option list and the
    connection fails outright with an opaque "connection failed".
    """
    if not schema or not url.startswith("postgresql"):
        return {}
    return {"connect_args": {"options": f"-csearch_path={_libpq_schema(schema)},public"}}


def _libpq_schema(schema: str) -> str:
    """Quote a schema name for `search_path`, then escape it for a libpq option."""
    quoted = '"' + schema.replace('"', '""') + '"'
    # libpq splits options on whitespace and treats backslash as an escape, so
    # both have to be escaped — backslashes first, or the escapes get escaped.
    return quoted.replace("\\", "\\\\").replace(" ", "\\ ")


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory


def get_session() -> Session:
    return get_session_factory()()


# Which tables this process has already ensured exist. Tracked as a set rather
# than a bool so that a process whose models gained a table after the first
# call — a `--reload` server that re-imported the models in place — does not
# short-circuit and leave the new table uncreated.
_initialized_tables: frozenset[str] = frozenset()


def init_db():
    """Create schema (if configured) and all tables.

    Safe to call repeatedly: work is skipped once every table the models
    currently define has been ensured in this process.
    """
    global _initialized_tables

    expected = frozenset(Base.metadata.tables)
    if expected <= _initialized_tables:
        return

    schema = get_settings().db_schema.strip()
    engine = get_engine()

    # get_engine() already points connections at the schema; it just may not
    # exist yet on a first run.
    if schema:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _initialized_tables = expected


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

    _widen_varchar_columns(engine)


def _widen_varchar_columns(engine) -> None:
    """Widen VARCHAR columns whose model length exceeds the DB column length."""
    from sqlalchemy import String

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        db_cols = {col["name"]: col for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in db_cols:
                continue
            model_type = column.type
            db_type = db_cols[column.name]["type"]
            if not isinstance(model_type, String) or model_type.length is None:
                continue
            db_length = getattr(db_type, "length", None)
            if db_length is not None and db_length < model_type.length:
                ddl = (
                    f'ALTER TABLE "{table.name}" '
                    f'ALTER COLUMN "{column.name}" TYPE varchar({model_type.length})'
                )
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                logger.info(
                    "Widened %s.%s from varchar(%d) to varchar(%d)",
                    table.name,
                    column.name,
                    db_length,
                    model_type.length,
                )
