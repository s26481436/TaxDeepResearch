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
        if ctx["parent_documents"]:
            typer.echo("母法 (子母法層級):")
            for ent in ctx["parent_documents"]:
                typer.echo(f"  ⇧ {ent.canonical_title} ({ent.entity_key})")
        if ctx["child_documents"]:
            typer.echo("子法 (施行細則/實施條例):")
            for ent in ctx["child_documents"]:
                typer.echo(f"  ⇩ {ent.canonical_title} ({ent.entity_key})")
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
def extract_requirements(
    document: str = typer.Argument(..., help="法規的 external_id 或標題"),
    country: str = typer.Option("CN", help="轄區代碼"),
    tax_key: str = typer.Option("", help="稅種鍵，留空則從標題推斷"),
    dry_run: bool = typer.Option(False, help="只顯示會抽出什麼，不寫入資料庫"),
):
    """從法規條文（含子法與公告）抽取申報規範。"""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.requirements.extract import NoSourceDocument, extract_for_document
    from taxwatch.services.documents import DocumentNotFound

    _init_db()
    session = get_session()
    try:
        stats = extract_for_document(
            session,
            document,
            country=country,
            tax_key=tax_key or None,
            dry_run=dry_run,
        )
    except (DocumentNotFound, NoSourceDocument) as exc:
        typer.echo(f"無法抽取：{exc}")
        raise typer.Exit(1) from None
    finally:
        session.close()

    typer.echo(
        f"\n稅種 {stats['tax_key']} — 依據《{stats['source_document']}》"
        f"（{stats['provisions_supplied']} 條）"
    )
    typer.echo(f"抽出 {stats['requirements']} 個課稅情境")
    if stats["dropped_citations"]:
        # The model naming provisions that were never supplied is the failure
        # mode worth shouting about — it means the guidance is partly invented.
        typer.echo(f"⚠ 捨棄 {stats['dropped_citations']} 筆指向不存在條文的引用")
    if stats["uncited_fields"]:
        typer.echo(f"⚠ {stats['uncited_fields']} 個欄位無條文依據，已標記待覆核")
    for item in stats["unresolved"]:
        typer.echo(f"  · 待人工補充：{item}")
    if dry_run:
        for row in stats.get("preview", []):
            typer.echo(f"  - {row['scenario']} / {row['taxpayer_role'] or '（未分身分）'}")


@app.command()
def import_requirements(
    path: str = typer.Argument(..., help="申報規範試算表（.xlsx）"),
    country: str = typer.Option("CN", help="轄區代碼"),
    sheet: str = typer.Option("", help="工作表名稱，留空取第一張"),
):
    """匯入財務彙整的申報規範試算表。"""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.requirements.importer import MissingDependency, import_workbook

    _init_db()
    session = get_session()
    try:
        stats = import_workbook(
            session,
            path,
            country=country,
            sheet=sheet or 0,
        )
    except MissingDependency as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    except ValueError as exc:
        typer.echo(f"無法匯入：{exc}")
        raise typer.Exit(1) from None
    finally:
        session.close()

    typer.echo(f"匯入 {stats['imported']} 列（略過 {stats['skipped']} 列）")
    typer.echo(f"對應到的欄位：{', '.join(stats['columns_mapped'])}")
    typer.echo("匯入內容尚未對應條文，已全數標記待覆核。")


@app.command()
def review_queue(
    tax_key: str = typer.Option("", help="只看單一稅種"),
):
    """列出待覆核的申報規範欄位。"""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.services.requirements import review_summary

    _init_db()
    session = get_session()
    try:
        summary = review_summary(session, tax_key=tax_key or None)
    finally:
        session.close()

    if not summary["count"]:
        typer.echo("沒有待覆核項目。")
        return

    typer.echo(f"\n{summary['count']} 個欄位待覆核\n")
    for item in summary["items"]:
        role = f" / {item['taxpayer_role']}" if item["taxpayer_role"] else ""
        typer.echo(f"[{item['tax_name']}] {item['scenario']}{role}")
        typer.echo(f"  {item['field_label']}: {item['reason']}")


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
