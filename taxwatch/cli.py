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
    tax: str = typer.Option("", "--tax", help="只跑指定稅種，逗號分隔（見 taxwatch tax-types）"),
):
    """Run the detection pipeline."""
    if not source and not all_sources:
        typer.echo("Specify --source <key> or --all")
        raise typer.Exit(1)

    tax_keys: list[str] | None = None
    if tax.strip():
        from taxwatch.taxonomy import TAX_TYPES, UNCLASSIFIED, by_key

        tax_keys = [k.strip() for k in tax.split(",") if k.strip()]
        valid_keys = [t.key for t in TAX_TYPES] + [UNCLASSIFIED.key]
        bad = [k for k in tax_keys if by_key(k) is None]
        if bad:
            typer.echo(f"Unknown tax key(s): {', '.join(bad)}")
            typer.echo(f"Valid keys: {', '.join(valid_keys)}")
            raise typer.Exit(1)

    from taxwatch.db import init_db as _init_db
    from taxwatch.jobs.pipeline import run_pipeline

    _init_db()

    run_pipeline(
        source_key=source,
        run_all=all_sources,
        stop_after=stage,
        tax_keys=tax_keys,
    )


@app.command(name="tax-types")
def tax_types_cmd():
    """List all recognised tax-type keys (for use with --tax)."""
    from taxwatch.taxonomy import TAX_TYPES, UNCLASSIFIED

    typer.echo(f"{'key':<25} {'name_zh':<15} keywords")
    typer.echo("-" * 70)
    for t in TAX_TYPES:
        kws = ", ".join(t.keywords[:4])
        if len(t.keywords) > 4:
            kws += f" … (+{len(t.keywords) - 4})"
        typer.echo(f"{t.key:<25} {t.name_zh:<15} {kws}")
    typer.echo(f"{UNCLASSIFIED.key:<25} {UNCLASSIFIED.name_zh:<15} (catch-all)")


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


_PRIMARY_LAW_SOURCE = {
    "CN": "cn-chinatax",
    "TW": "tw-moj-law",
    "US": "us-ecfr",
}


def _echo_identity_report(stats: dict) -> None:
    """Say how many rows lack a controlled identity, and why.

    Shared by both entry paths. The tax-keyed path aggregated counts from each
    document but printed none of this, so a run where every row fell back to
    text identity looked indistinguishable from a clean one — which is the one
    fact this feature exists to expose.
    """
    without = stats.get("rows_without_identity", 0)
    if without:
        emitted = stats.get("requirements_emitted", 0) or without
        typer.echo(
            f"\n⚠ {without}/{emitted} 列沒有受控身分（identity_key），"
            "將以情境描述文字作為身分。"
            "\n   描述文字每次抽取都會變動，這些列在重跑時不會被更新，而會新增為另一列。"
        )
    for key, label in (
        ("unknown_dimension_values", "未知"),
        ("incomplete_dimensions", "缺漏"),
    ):
        items = stats.get(key) or []
        if not items:
            continue
        typer.echo(f"⚠ {len(items)} 個{label}的身分維度值（已標記待覆核）：")
        for item in items[:20]:
            typer.echo(f"  · {item}")
        if len(items) > 20:
            typer.echo(f"  · …另有 {len(items) - 20} 筆")



def _echo_failed_batches(stats: dict) -> None:
    """A partial matrix must announce its own gaps, or it reads as complete."""
    failed = stats.get("failed_batches") or []
    if not failed:
        return
    total = stats.get("batches") or len(failed)
    typer.echo(f"\n⚠ {len(failed)}/{total} 個批次失敗，以下條文未納入分析：")
    for f in failed:
        typer.echo(f"  · 批次 #{f['batch']}：{f['error']}")
    typer.echo("  結果不完整。重跑本指令即可補齊，已產出的規範不會遺失。")


def _primary_source(country: str) -> str:
    return _PRIMARY_LAW_SOURCE.get(country.upper(), "--all")


def _echo_ingest_hint(country: str) -> None:
    typer.echo(f"\n請先擷取母法：taxwatch run --source {_primary_source(country)}")


@app.command()
def extract_requirements(
    tax_or_doc: str = typer.Argument(..., help="稅種代碼（如 cn_vat）或法規 external_id / 標題"),
    country: str = typer.Option("", help="轄區代碼，留空則自動判定"),
    tax_key: str = typer.Option("", help="稅種鍵，留空則自動推斷"),
    dry_run: bool = typer.Option(False, help="只顯示會抽出什麼，不寫入資料庫"),
    allow_child: bool = typer.Option(
        False,
        "--allow-child",
        help="母法未收錄時，仍允許只依子法（實施條例等）抽取",
    ),
):
    """從稅種或法規條文（含子法與公告）抽取申報規範。

    TAX_OR_DOC 可以是稅種代碼（例如 cn_vat）、external_id、完整標題，或標題的一段。
    """
    from openai import APIError

    from taxwatch.config import get_settings
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.requirements.extract import (
        CountryMismatch,
        MissingParentLaw,
        NoSourceDocument,
        extract_for_document,
        extract_for_tax,
    )
    from taxwatch.services.documents import (
        AmbiguousDocument,
        DocumentNotFound,
        ParentLawMissing,
        suggest_documents,
    )
    from taxwatch.taxonomy import by_key

    _init_db()
    session = get_session()
    try:
        # Check if tax_or_doc is a known tax_key
        if by_key(tax_or_doc):
            stats = extract_for_tax(
                session,
                tax_or_doc,
                country=country or None,
                dry_run=dry_run,
                allow_child=allow_child,
            )
            typer.echo(
                f"\n稅種 {stats['tax_key']} ({stats['tax_name']}) — 處理 {stats['documents_processed']} 部法規"
            )
            for doc_title in stats.get("source_documents", []):
                typer.echo(f"  ├ 法規：{doc_title}")
            typer.echo(f"抽出 {stats['requirements']} 個課稅情境")
            for r in stats.get("results", []):
                _echo_failed_batches(r)
            _echo_identity_report(stats)
            if dry_run:
                for row in stats.get("preview", []):
                    typer.echo(f"  - {row['scenario']} / {row['taxpayer_role'] or '（未分身分）'}")
            if stats["dropped_citations"]:
                typer.echo(f"⚠ 捨棄 {stats['dropped_citations']} 筆指向不存在條文的引用")
            if stats["uncited_fields"]:
                typer.echo(f"⚠ {stats['uncited_fields']} 個欄位無條文依據，已標記待覆核")
            return
        else:
            stats = extract_for_document(
                session,
                tax_or_doc,
                country=country or None,
                tax_key=tax_key or None,
                dry_run=dry_run,
                allow_child=allow_child,
            )
    except CountryMismatch as exc:
        typer.echo(f"轄區不一致：指定了 {exc.expected}，但該法規來源屬於 {exc.actual}。")
        raise typer.Exit(1) from None
    except AmbiguousDocument as exc:
        typer.echo(f"「{exc.term}」對應到多份法規，請指定其中一份：")
        for title in exc.candidates:
            typer.echo(f"  · {title}")
        raise typer.Exit(1) from None
    except ParentLawMissing as exc:
        typer.echo(f"資料庫裡沒有母法《{exc.term}》，只有它的子法：")
        for title in exc.children:
            typer.echo(f"  · {title}")
        _echo_ingest_hint(country)
        typer.echo(
            f"若確定要只依子法抽取：taxwatch extract-requirements {exc.children[0]} --allow-child"
        )
        raise typer.Exit(1) from None
    except MissingParentLaw as exc:
        reason = "尚未收錄" if exc.status == "missing" else "已收錄但條文未解析"
        typer.echo(f"《{exc.child_title}》是子法，其母法《{exc.parent_key}》{reason}。")
        typer.echo("子法只定義母法用語，不含納稅義務人、課稅範圍與申報期限，")
        typer.echo("單獨抽取會產出沒有條文依據的課稅情境，因此中止。")
        if exc.status == "missing":
            _echo_ingest_hint(country)
        else:
            typer.echo(f"\n請重新擷取母法：taxwatch run --source {_primary_source(country)}")
        typer.echo("仍要只依子法抽取請加上 --allow-child")
        raise typer.Exit(1) from None
    except DocumentNotFound:
        # A bare "not found" is useless when the ids are machine-minted
        # (c5251620, 文號) and nobody can guess one.
        typer.echo(f"找不到法規或稅種：{tax_or_doc}")
        candidates = suggest_documents(session, tax_or_doc)
        if candidates:
            typer.echo("\n已收錄的法規（可用 external_id 或標題片段）：")
            for c in candidates:
                typer.echo(f"  [{c['country']}] {c['external_id']:<28} {c['title']}")
            typer.echo("\n完整清單：taxwatch documents")
        else:
            typer.echo("\n資料庫裡還沒有任何法規，請先執行：taxwatch run --source cn-chinatax")
        raise typer.Exit(1) from None
    except NoSourceDocument as exc:
        typer.echo(f"沒有已解析的條文，無法抽取：{exc}")
        raise typer.Exit(1) from None
    except APIError as exc:
        # A stack trace from inside the OpenAI SDK tells the reader nothing
        # about the one thing they can act on: their LLM settings.
        settings = get_settings()
        typer.echo(f"LLM 呼叫失敗：{type(exc).__name__}")
        typer.echo(f"  LLM_BASE_URL  {settings.llm_base_url}")
        typer.echo(f"  LLM_MODEL     {settings.llm_model}")
        typer.echo("  請確認服務已啟動、位址正確，且 .env 的 LLM_* 設定無誤。")
        raise typer.Exit(1) from None
    finally:
        session.close()

    typer.echo(
        f"\n稅種 {stats['tax_key']} — 依據《{stats['source_document']}》"
        f"（{stats['provisions_supplied']} 條）"
    )
    if stats.get("missing_parent"):
        typer.echo(f"⚠ 母法《{stats['missing_parent']['key']}》缺漏，本次僅依子法抽取")
    for child in stats.get("child_documents", []):
        typer.echo(f"  ├ 子法：{child}")
    typer.echo(f"抽出 {stats['requirements']} 個課稅情境")
    _echo_failed_batches(stats)
    if stats["requirements"] == 0:
        typer.echo("\n⚠ 0 個情境 — 成因分解：")
        typer.echo(f"  母法條文：{stats['provisions_supplied']} 條送入 LLM")
        typer.echo(f"  子法：{len(stats.get('child_documents', []))} 份")
        if stats.get("truncated_nodes"):
            typer.echo(f"  ⚠ 被預算截斷：{stats['truncated_nodes']} 條（超出 prompt 長度上限）")
        typer.echo("  可能原因：LLM 回傳的 JSON 缺少 requirements 鍵、或 llm_max_tokens 不足")
    if stats["dropped_citations"]:
        # The model naming provisions that were never supplied is the failure
        # mode worth shouting about — it means the guidance is partly invented.
        typer.echo(f"⚠ 捨棄 {stats['dropped_citations']} 筆指向不存在條文的引用")
    if stats["uncited_fields"]:
        typer.echo(f"⚠ {stats['uncited_fields']} 個欄位無條文依據，已標記待覆核")
    _echo_identity_report(stats)
    for item in stats["unresolved"]:
        typer.echo(f"  · 待人工補充：{item}")
    if dry_run:
        for row in stats.get("preview", []):
            id_key = row.get("identity_key")
            if id_key:
                typer.echo(f"  - {id_key}")
                typer.echo(f"      {row['scenario']} / {row['taxpayer_role'] or '（未分身分）'}")
            else:
                typer.echo(f"  - {row['scenario']} / {row['taxpayer_role'] or '（未分身分）'}")


@app.command()
def import_requirements(
    path: str = typer.Argument(..., help="申報規範檔案（.xlsx、.md、.csv）"),
    country: str = typer.Option("CN", help="轄區代碼"),
    sheet: str = typer.Option("", help="工作表名稱（僅 .xlsx），留空取第一張"),
    source_note: str = typer.Option("", help="來源說明（如 gpt-researcher 2026-08-19）"),
):
    """匯入財務或研究彙整的申報規範表（.xlsx / .md / .csv）。"""
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
            source_note=source_note,
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
    if stats.get("unmapped_headers"):
        typer.echo(f"⚠ 未對應的表頭（已略過）：{', '.join(stats['unmapped_headers'])}")
    if stats.get("missing_identity_columns"):
        typer.echo(
            f"⚠⚠ 缺少身分欄位：{', '.join(stats['missing_identity_columns'])}。"
            "\n   稅種將全部歸入「其他」，且納稅人身分為空——不同情境會共用同一身分，"
            "\n   後續重新匯入可能互相覆蓋。請確認表頭後重新匯入。"
        )
    if stats.get("citations_resolved", 0) or stats.get("citations_unresolved", 0):
        typer.echo(
            f"條文引用解析：成功對應 {stats['citations_resolved']} 筆條文引用，"
            f"無法對應 {stats['citations_unresolved']} 筆"
        )
    typer.echo("匯入內容尚未經人工確認，已全數標記待覆核。")


@app.command()
def review_queue(
    country: str = typer.Option("", help="只看單一轄區"),
    tax_key: str = typer.Option("", help="只看單一稅種"),
):
    """列出待覆核的申報規範欄位。"""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.services.requirements import review_summary

    _init_db()
    session = get_session()
    try:
        summary = review_summary(session, country=country or None, tax_key=tax_key or None)
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
def documents(
    country: str = typer.Option("", help="只看單一轄區，例如 CN／TW／US"),
    search: str = typer.Option("", help="標題包含此字串"),
    limit: int = typer.Option(50, help="最多顯示幾筆"),
):
    """列出已收錄的法規，以及可用於其他指令的 external_id。"""
    from taxwatch.db import get_session
    from taxwatch.db import init_db as _init_db
    from taxwatch.models import Document, Snapshot, Source

    _init_db()
    session = get_session()
    try:
        query = session.query(Document, Source).join(Source, Document.source_id == Source.id)
        if country:
            query = query.filter(Source.country == country.upper())
        if search:
            query = query.filter(Document.title.contains(search))

        rows = query.limit(limit).all()
        if not rows:
            typer.echo("沒有符合的法規。尚未抓取的話請先執行：taxwatch run --all")
            return

        typer.echo(f"\n{len(rows)} 份法規\n")
        for doc, source in rows:
            provisions = (
                session.query(Snapshot)
                .filter_by(document_id=doc.id)
                .order_by(Snapshot.id.desc())
                .first()
            )
            count = len(provisions.provisions) if provisions else 0
            typer.echo(f"  [{source.country}] {doc.external_id}")
            typer.echo(f"        {doc.title}")
            typer.echo(f"        {doc.doc_type.value} · {count} 條 · {source.key}")
    finally:
        session.close()


@app.command()
def doctor(
    fix: bool = typer.Option(False, help="嘗試建立缺少的資料表與欄位"),
):
    """診斷「relation does not exist」這類資料庫／版本不一致問題。

    印出實際生效的設定、連到哪個資料庫、search_path 落在哪個 schema，
    以及執行中的程式碼期待哪些資料表、資料庫實際有哪些。這些對不上時，
    問題幾乎都在其中之一：跑的是舊的已安裝副本、連到別的資料庫，
    或資料表建在另一個 schema。
    """
    from pathlib import Path

    from sqlalchemy import inspect, text

    import taxwatch
    from taxwatch.config import get_settings
    from taxwatch.db import get_engine, get_session
    from taxwatch.models import Base

    settings = get_settings()

    typer.echo("\n=== 執行中的程式碼 ===")
    typer.echo(f"  套件位置    {Path(taxwatch.__file__).parent}")
    typer.echo(f"  Python      {sys.executable}")
    # An editable install points into the working tree; a plain `pip install .`
    # copies into site-packages, where `git pull` never reaches it.
    in_site_packages = "site-packages" in str(Path(taxwatch.__file__).resolve())
    install = (
        "複製到 site-packages（git pull 不會更新）" if in_site_packages else "editable／原始碼目錄"
    )
    typer.echo(f"  安裝方式    {install}")

    typer.echo("\n=== 資料庫連線 ===")
    typer.echo(f"  DATABASE_URL  {_redact(settings.database_url)}")
    typer.echo(f"  DB_SCHEMA     {settings.db_schema or '(未設定，使用 public)'}")

    engine = get_engine()
    try:
        session = get_session()
        with session.connection() as conn:
            server = conn.execute(text("SELECT version()")).scalar() or ""
            current_db = conn.execute(text("SELECT current_database()")).scalar()
            search_path = conn.execute(text("SHOW search_path")).scalar()
            current_schema = conn.execute(text("SELECT current_schema()")).scalar()
        session.close()
    except Exception as exc:
        typer.echo(f"  ✗ 無法連線：{type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None

    typer.echo(f"  已連線        {server.split(',')[0]}")
    typer.echo(f"  current_database  {current_db}")
    typer.echo(f"  search_path       {search_path}")
    typer.echo(f"  current_schema    {current_schema}")

    expected = set(Base.metadata.tables)
    inspector = inspect(engine)
    actual = set(inspector.get_table_names())
    missing = sorted(expected - actual)

    typer.echo(f"\n=== 資料表（於 schema {current_schema}）===")
    typer.echo(f"  程式碼期待  {len(expected)}")
    typer.echo(f"  資料庫實際  {len(actual & expected)}")

    if missing:
        typer.echo(f"  ✗ 缺少 {len(missing)}：{', '.join(missing)}")
        # A table living in another schema is the classic search_path symptom;
        # saying so beats letting someone re-run migrations that already ran.
        elsewhere = _tables_in_other_schemas(inspector, missing, current_schema)
        for table, schemas in elsewhere.items():
            typer.echo(f"    · {table} 其實存在於 schema：{', '.join(schemas)}")
        if elsewhere:
            typer.echo("    → search_path 指向的 schema 與資料表所在的不同，請檢查 DB_SCHEMA")
    else:
        typer.echo("  ✓ 全部存在")

    missing_columns = _missing_columns(inspector, expected & actual)
    if missing_columns:
        typer.echo(f"\n  ✗ 缺少欄位：{'; '.join(missing_columns)}")

    if (missing or missing_columns) and fix:
        from taxwatch.db import init_db as _init_db

        typer.echo("\n=== 建立缺少的資料表與欄位 ===")
        _init_db()
        remaining = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
        typer.echo("  ✓ 完成" if not remaining else f"  ✗ 仍缺少：{sorted(remaining)}")
    elif missing or missing_columns:
        typer.echo("\n  以 --fix 建立缺少的項目（或重新啟動 taxwatch serve）")


def _redact(url: str) -> str:
    import re

    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def _tables_in_other_schemas(inspector, missing: list[str], current: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    try:
        schemas = [s for s in inspector.get_schema_names() if s != current]
    except Exception:
        return found
    for schema in schemas:
        try:
            names = set(inspector.get_table_names(schema=schema))
        except Exception:
            continue
        for table in missing:
            if table in names:
                found.setdefault(table, []).append(schema)
    return found


def _missing_columns(inspector, tables: set[str]) -> list[str]:
    from taxwatch.models import Base

    gaps: list[str] = []
    for name in sorted(tables):
        table = Base.metadata.tables[name]
        present = {c["name"] for c in inspector.get_columns(name)}
        absent = [c.name for c in table.columns if c.name not in present]
        if absent:
            gaps.append(f"{name}.{{{','.join(absent)}}}")
    return gaps


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
