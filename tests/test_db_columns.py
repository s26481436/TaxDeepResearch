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


def _legacy_requirements_table(engine) -> None:
    """tax_requirements as it stood before the controlled-identity columns."""
    legacy = MetaData()
    Table(
        "tax_requirements",
        legacy,
        Column("id", Integer, primary_key=True),
        Column("country", String(10)),
        Column("tax_key", String(50)),
        Column("scenario", String),
        Column("taxpayer_role", String),
        Column("status", String(20)),
    )
    legacy.create_all(engine)


class TestNotNullColumns:
    """`Mapped[str]` is NOT NULL, and the backfill used to skip those in silence.

    `identity_key` and `dimensions` were therefore never added to any database
    created before them, and the omission surfaced only when a query failed
    with UndefinedColumn — nothing in the upgrade said anything was wrong.
    """

    def test_a_not_null_column_is_added(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        _legacy_requirements_table(engine)
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    "INSERT INTO tax_requirements (country, tax_key, scenario, status) "
                    "VALUES ('TW', 'tw_income', 's', 'draft')"
                )
            )

        _add_missing_columns(engine)

        columns = {c["name"] for c in inspect(engine).get_columns("tax_requirements")}
        assert "identity_key" in columns
        assert "dimensions" in columns

    def test_existing_rows_are_backfilled_with_the_model_default(self, tmp_path):
        """Adding NOT NULL without a default would fail outright on a populated table."""
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        _legacy_requirements_table(engine)
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    "INSERT INTO tax_requirements (country, tax_key, scenario, status) "
                    "VALUES ('TW', 'tw_income', 's', 'draft')"
                )
            )

        _add_missing_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(
                sql_text("SELECT identity_key, dimensions FROM tax_requirements")
            ).one()
        assert row[0] == ""
        assert row[1] == "{}"


class TestDefaultLiteral:
    def test_a_string_default_becomes_a_quoted_literal(self):
        from sqlalchemy import Column as Col
        from sqlalchemy import String as Str

        from taxwatch.db import _default_literal

        assert _default_literal(Col("x", Str, default="")) == "''"
        assert _default_literal(Col("x", Str, default="draft")) == "'draft'"

    def test_an_embedded_quote_is_escaped(self):
        from sqlalchemy import Column as Col
        from sqlalchemy import String as Str

        from taxwatch.db import _default_literal

        assert _default_literal(Col("x", Str, default="it's")) == "'it''s'"

    def test_json_container_defaults_are_recognised(self):
        from sqlalchemy import JSON
        from sqlalchemy import Column as Col

        from taxwatch.db import _default_literal

        assert _default_literal(Col("x", JSON, default=dict)) == "'{}'"
        assert _default_literal(Col("x", JSON, default=list)) == "'[]'"

    def test_a_row_dependent_default_yields_nothing(self):
        """`utcnow` describes the insert, not the rows already there."""
        from datetime import datetime

        from sqlalchemy import Column as Col
        from sqlalchemy import DateTime as Dt

        from taxwatch.db import _default_literal

        assert _default_literal(Col("x", Dt, default=datetime.utcnow)) is None

    def test_no_default_yields_nothing(self):
        from sqlalchemy import Column as Col
        from sqlalchemy import String as Str

        from taxwatch.db import _default_literal

        assert _default_literal(Col("x", Str)) is None


def test_a_not_null_column_without_a_default_is_added_nullable(tmp_path):
    """Permissive beats absent: every insert supplies the model default anyway."""
    from sqlalchemy import Column as Col
    from sqlalchemy import Integer as Int
    from sqlalchemy import String as Str

    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    old = MetaData()
    Table("widgets", old, Col("id", Int, primary_key=True))
    old.create_all(engine)

    new = MetaData()
    Table("widgets", new, Col("id", Int, primary_key=True), Col("label", Str, nullable=False))

    from unittest.mock import patch

    with patch("taxwatch.db.Base") as base:
        base.metadata.sorted_tables = list(new.tables.values())
        _add_missing_columns(engine)

    columns = {c["name"]: c for c in inspect(engine).get_columns("widgets")}
    assert "label" in columns
    assert columns["label"]["nullable"] is True


def test_a_column_that_cannot_be_added_is_named_at_startup(tmp_path, caplog):
    """One UndefinedColumn from inside a run names one column; this names all."""
    import logging
    from unittest.mock import patch

    from sqlalchemy import Column as Col
    from sqlalchemy import Integer as Int
    from sqlalchemy import String as Str

    from taxwatch.db import _report_columns_still_missing

    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    old = MetaData()
    Table("widgets", old, Col("id", Int, primary_key=True))
    old.create_all(engine)

    new = MetaData()
    Table(
        "widgets",
        new,
        Col("id", Int, primary_key=True),
        Col("label", Str),
        Col("colour", Str),
    )

    with patch("taxwatch.db.Base") as base, caplog.at_level(logging.ERROR):
        base.metadata.sorted_tables = list(new.tables.values())
        _report_columns_still_missing(engine)

    assert "widgets.label" in caplog.text
    assert "widgets.colour" in caplog.text
