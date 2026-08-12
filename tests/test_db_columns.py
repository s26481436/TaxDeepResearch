"""The project has no migration tool, so `init_db` backfills new columns itself.

Without this, upgrading against a populated database fails at the first query
naming a column the models gained — and the only remedy would be dropping the
crawl history.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect
from sqlalchemy import text as sql_text

from taxwatch.db import _add_missing_columns


def _legacy_snapshots_table(engine) -> None:
    """The snapshots table as it stood before `issued_at` existed."""
    legacy = MetaData()
    Table(
        "snapshots",
        legacy,
        Column("id", Integer, primary_key=True),
        Column("document_id", Integer, nullable=False),
        Column("fetched_at", DateTime),
        Column("content_hash", String(64)),
        Column("raw_path", String),
    )
    legacy.create_all(engine)


def test_adds_column_missing_from_an_existing_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _legacy_snapshots_table(engine)
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO snapshots (document_id, content_hash, raw_path) VALUES (1, 'abc', '')"
            )
        )

    _add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("snapshots")}
    assert "issued_at" in columns

    with engine.connect() as conn:
        row = conn.execute(sql_text("SELECT content_hash, issued_at FROM snapshots")).one()
    assert row == ("abc", None)


def test_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _legacy_snapshots_table(engine)

    _add_missing_columns(engine)
    _add_missing_columns(engine)

    columns = [c["name"] for c in inspect(engine).get_columns("snapshots")]
    assert columns.count("issued_at") == 1


def test_leaves_a_current_schema_untouched(tmp_path):
    from taxwatch.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    Base.metadata.create_all(engine)
    before = {
        table: [c["name"] for c in inspect(engine).get_columns(table)]
        for table in inspect(engine).get_table_names()
    }

    _add_missing_columns(engine)

    after = {
        table: [c["name"] for c in inspect(engine).get_columns(table)]
        for table in inspect(engine).get_table_names()
    }
    assert before == after


class TestSchemaConnectArgs:
    """`search_path` must be set during connection setup, not after.

    A `connect` event listener runs after SQLAlchemy has already resolved
    `dialect.default_schema_name`, so the dialect records `public` and every
    later `get_table_names()` reads the wrong schema — a populated database
    reports as empty.
    """

    def test_postgres_url_gets_the_search_path_option(self):
        from taxwatch.db import _schema_connect_args

        args = _schema_connect_args("postgresql+psycopg://u@h/db", "taxwatch_prod")
        assert args == {"connect_args": {"options": '-csearch_path="taxwatch_prod",public'}}

    def test_hyphenated_schema_is_quoted(self):
        """`fin-tax` happens to survive unquoted, but only by luck."""
        from taxwatch.db import _schema_connect_args

        args = _schema_connect_args("postgresql+psycopg://u@h/db", "fin-tax")
        assert args["connect_args"]["options"] == '-csearch_path="fin-tax",public'

    def test_schema_with_a_space_is_escaped_for_libpq(self):
        """Unescaped, the space splits the option list and the connect fails
        with an opaque "connection failed" rather than anything diagnosable."""
        from taxwatch.db import _schema_connect_args

        args = _schema_connect_args("postgresql+psycopg://u@h/db", "fin tax")
        assert args["connect_args"]["options"] == '-csearch_path="fin\\ tax",public'

    def test_embedded_quote_is_doubled(self):
        from taxwatch.db import _libpq_schema

        assert _libpq_schema('od"d') == '"od""d"'

    def test_no_schema_configured_adds_nothing(self):
        from taxwatch.db import _schema_connect_args

        assert _schema_connect_args("postgresql+psycopg://u@h/db", "") == {}

    def test_non_postgres_backend_is_left_alone(self):
        """SQLite has no search_path, and libpq options would break the connect."""
        from taxwatch.db import _schema_connect_args

        assert _schema_connect_args("sqlite:///x.db", "taxwatch_prod") == {}
