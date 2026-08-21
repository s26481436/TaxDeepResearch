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
            # An extraction run holds a session across every LLM call for the
            # tax — minutes to tens of minutes once retries and gateway
            # suspension backoffs are counted. A pooled connection is long dead
            # by the time the writes begin, and the failure surfaces as
            # "server closed the connection unexpectedly" on an ordinary SELECT.
            # Pre-ping validates on checkout and reconnects transparently.
            pool_pre_ping=True,
            pool_recycle=settings.db_pool_recycle,
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


def _default_literal(column) -> str | None:
    """SQL literal that can backfill existing rows when adding a NOT NULL column.

    Only values the model states outright. A callable default may depend on the
    row being inserted, so it cannot stand in for rows that already exist — the
    two exceptions are `dict` and `list`, whose empty forms are exactly what the
    Python-side default would have produced.
    """
    from sqlalchemy import JSON

    default = column.default
    if default is None:
        return None

    if getattr(default, "is_callable", False):
        arg = getattr(default, "arg", None)
        target = getattr(arg, "__wrapped__", arg)
        if isinstance(column.type, JSON):
            if target is dict:
                return "'{}'"
            if target is list:
                return "'[]'"
        return None

    value = getattr(default, "arg", None)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def _add_missing_columns(engine) -> None:
    """Add nullable columns the models gained since a database was created.

    `create_all` creates missing *tables* but never alters existing ones, and
    the project carries no migration tool. Without this, upgrading against a
    populated database fails at the first query naming a new column — with the
    only remedy being to drop the crawl history and start over.

    Deliberately narrow: adds only. Anything else (drops, type changes,
    constraints) needs a real migration and should not be smuggled in here.

    A NOT NULL column is added with its model default as a server default, so
    existing rows get a value in the same statement. Where no safe default can
    be derived the column is added nullable rather than skipped: every insert
    supplies a value through the model default anyway, and a column that is
    merely permissive beats one that does not exist. Skipping is what this used
    to do, and `identity_key` — `Mapped[str]`, therefore NOT NULL — was passed
    over in silence until a query failed with UndefinedColumn much later.
    """
    inspector = inspect(engine)

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or column.primary_key:
                continue
            ddl = (
                f'ALTER TABLE "{table.name}" '
                f'ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
            )
            note = ""
            if not column.nullable:
                literal = _default_literal(column)
                if literal is None:
                    note = " (as nullable: no safe default for a NOT NULL add)"
                    logger.warning(
                        "Adding %s.%s as nullable — the model declares it NOT NULL but "
                        "supplies no literal default to backfill existing rows with.",
                        table.name,
                        column.name,
                    )
                else:
                    ddl += f" DEFAULT {literal} NOT NULL"
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info("Added missing column %s.%s%s", table.name, column.name, note)

    _widen_varchar_columns(engine)
    _report_columns_still_missing(engine)


def _report_columns_still_missing(engine) -> None:
    """Complain at startup about columns the backfill could not add.

    The previous silent skip turned a schema gap into an UndefinedColumn raised
    from deep inside an extraction run, naming one column out of however many
    were missing. Whatever cannot be added should be said here, once, in full.
    """
    inspector = inspect(engine)
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        missing.extend(
            f"{table.name}.{column.name}"
            for column in table.columns
            if column.name not in existing
        )
    if missing:
        logger.error(
            "Schema is missing %d column(s) the models require: %s. "
            "Queries naming them will fail.",
            len(missing),
            ", ".join(missing),
        )


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
