from __future__ import annotations

import sys

# Ensure UTF-8 stdout/stderr on Windows (cp950 default breaks Chinese output)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import typer

app = typer.Typer(help="TaxWatch — 稅法異動自動化偵測系統")


@app.command()
def init_db():
    """Initialize database tables."""
    from taxwatch.db import init_db as _init_db

    _init_db()
    typer.echo("Database tables created.")


@app.command()
def seed_sources():
    """Load sources from config/sources.yaml into the database."""
    from taxwatch.config import load_sources
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db

    _init_db()
    from taxwatch.models import Source

    sources = load_sources()
    session = get_session()
    try:
        for key, cfg in sources.items():
            existing = session.query(Source).filter_by(key=key).first()
            if existing:
                existing.country = cfg["country"]
                existing.connector = cfg["connector"]
                existing.description = cfg.get("description", "")
                existing.config = cfg.get("config", {})
                existing.enabled = cfg.get("enabled", True)
            else:
                session.add(
                    Source(
                        key=key,
                        country=cfg["country"],
                        connector=cfg["connector"],
                        description=cfg.get("description", ""),
                        config=cfg.get("config", {}),
                        enabled=cfg.get("enabled", True),
                    )
                )
        session.commit()
        typer.echo(f"Seeded {len(sources)} sources.")
    finally:
        session.close()


@app.command()
def run(
    source: str | None = typer.Option(None, help="Source key to run"),
    all_sources: bool = typer.Option(False, "--all", help="Run all enabled sources"),
    stage: str | None = typer.Option(None, help="Stop after stage: fetch, diff, graph, analyze"),
):
    """Run the detection pipeline."""
    from taxwatch.db import init_db as _init_db
    from taxwatch.jobs.pipeline import run_pipeline

    _init_db()

    if not source and not all_sources:
        typer.echo("Specify --source <key> or --all")
        raise typer.Exit(1)

    run_pipeline(source_key=source, run_all=all_sources, stop_after=stage)


@app.command()
def graph_show(
    entity: str = typer.Argument(..., help="Entity key, e.g. 所得稅法#14"),
):
    """Show legal graph relations for an entity."""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.graph.relations import get_entity_context

    _init_db()

    session = get_session()
    try:
        ctx = get_entity_context(session, entity)
        if not ctx:
            typer.echo(f"Entity not found: {entity}")
            raise typer.Exit(1)
        typer.echo(f"\n=== {ctx['entity'].canonical_title} ({ctx['entity'].entity_key}) ===\n")
        if ctx["parent_laws"]:
            typer.echo("母法:")
            for rel, ent in ctx["parent_laws"]:
                rtype = rel.relation_type.value
                typer.echo(f"  ← {rtype}: {ent.canonical_title} ({ent.entity_key})")
        if ctx["children"]:
            typer.echo("子項 (函釋/釋字):")
            for rel, ent in ctx["children"]:
                rtype = rel.relation_type.value
                typer.echo(f"  → {rtype}: {ent.canonical_title} ({ent.entity_key})")
        if ctx["siblings"]:
            typer.echo("同條文相關:")
            for ent in ctx["siblings"]:
                typer.echo(f"  ~ {ent.canonical_title} ({ent.entity_key})")
    finally:
        session.close()


@app.command()
def report(
    format: str = typer.Option("markdown", help="Output format: markdown, html"),
    out: str = typer.Option("reports/", help="Output directory"),
    days: int = typer.Option(7, help="Include changes from last N days"),
):
    """Generate change report."""
    from pathlib import Path

    from taxwatch.db import init_db as _init_db
    from taxwatch.report.markdown import generate_report

    _init_db()

    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)
    generate_report(outdir, days=days, fmt=format)
    typer.echo(f"Report written to {outdir}")


@app.command()
def import_corpus(
    path: str = typer.Argument(..., help="Path to the corpus .parquet file"),
    corpus_key: str = typer.Option("chinatax", help="Identifier for this corpus"),
    version: str = typer.Option("", help="Corpus snapshot date, e.g. 2026-02-27"),
    base_url: str = typer.Option("https://fgk.chinatax.gov.cn", help="Base URL for relative links"),
):
    """Import a reference corpus used to resolve citations without web searches.

    Check the corpus licence before use — the chinatax policy corpus is
    CC-BY-NC-4.0 (non-commercial). Imported data stays local and is never
    redistributed by TaxWatch.
    """
    from pathlib import Path

    from taxwatch.corpus.loader import import_corpus as run_import
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db

    _init_db()
    session = get_session()
    try:
        stats = run_import(
            session,
            Path(path),
            corpus_key=corpus_key,
            corpus_version=version,
            base_url=base_url,
        )
    finally:
        session.close()

    typer.echo(
        f"Imported {stats['stored']:,} documents into corpus '{corpus_key}' "
        f"({stats['with_document_number']:,} with a 文號)"
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8000, help="Port to listen on"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
):
    """Serve the web dashboard (and JSON API) on http://host:port."""
    import uvicorn

    uvicorn.run("taxwatch.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
