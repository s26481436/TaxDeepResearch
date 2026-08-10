"""Generate Markdown change reports."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from taxwatch.db import get_session
from taxwatch.models import Analysis, Change, Document, Severity, Source


def generate_report(outdir: Path, days: int = 7, fmt: str = "markdown"):
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        changes = (
            session.query(Change)
            .filter(Change.detected_at >= cutoff)
            .order_by(Change.detected_at.desc())
            .all()
        )

        if not changes:
            content = _empty_report(days)
        else:
            content = _build_report(session, changes, days)

        timestamp = datetime.utcnow().strftime("%Y%m%d")
        if fmt == "markdown":
            filepath = outdir / f"taxwatch-report-{timestamp}.md"
        else:
            filepath = outdir / f"taxwatch-report-{timestamp}.html"
            content = _wrap_html(content)

        filepath.write_text(content, encoding="utf-8")
    finally:
        session.close()


def _empty_report(days: int) -> str:
    return f"# TaxWatch 異動報告\n\n過去 {days} 天內無偵測到異動。\n"


def _build_report(session: Session, changes: list[Change], days: int) -> str:
    lines: list[str] = []
    lines.append("# TaxWatch 稅法異動報告\n")
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"期間：過去 {days} 天 | 產出時間：{now_str}\n")
    lines.append(f"共偵測到 **{len(changes)}** 筆異動\n")
    lines.append("> ⚠️ 本報告為 AI 輔助生成之參考資料，非法律意見。\n")

    by_country: dict[str, list[Change]] = {}
    for ch in changes:
        doc = session.get(Document, ch.document_id)
        if doc:
            src = session.get(Source, doc.source_id)
            country = src.country if src else "??"
        else:
            country = "??"
        by_country.setdefault(country, []).append(ch)

    severity_order = {
        Severity.CRITICAL: 0, Severity.MAJOR: 1,
        Severity.MINOR: 2, Severity.COSMETIC: 3,
    }

    for country, country_changes in sorted(by_country.items()):
        lines.append(f"\n## {_country_name(country)}\n")
        sorted_changes = sorted(country_changes, key=lambda c: severity_order.get(c.severity, 9))

        for ch in sorted_changes:
            doc = session.get(Document, ch.document_id)
            doc_title = doc.title if doc else ch.node_key
            severity_badge = _severity_badge(ch.severity)

            lines.append(f"### {severity_badge} {doc_title} — {ch.node_key}\n")
            lines.append(f"- 異動類型：{ch.change_type.value}")
            lines.append(f"- 偵測時間：{ch.detected_at.strftime('%Y-%m-%d')}")

            analysis = session.query(Analysis).filter_by(change_id=ch.id).first()
            if analysis:
                lines.append(f"\n**摘要**：{analysis.summary_zh}\n")
                if analysis.effective_date:
                    lines.append(f"- 生效日：{analysis.effective_date}")
                if analysis.affected_parties:
                    lines.append(f"- 受影響對象：{', '.join(analysis.affected_parties)}")
                if analysis.parent_law_impact:
                    lines.append(f"\n**母法影響**：{analysis.parent_law_impact}\n")
                lines.append(f"- 信心度：{analysis.confidence:.0%}")

            if ch.diff_text:
                lines.append(f"\n<details><summary>Diff</summary>\n\n```diff\n{ch.diff_text}\n```\n</details>\n")

            lines.append("---\n")

    return "\n".join(lines)


def _country_name(code: str) -> str:
    return {"TW": "台灣 🇹🇼", "US": "美國 🇺🇸", "CN": "中國 🇨🇳"}.get(code, code)


def _severity_badge(sev: Severity) -> str:
    return {
        Severity.CRITICAL: "🔴 CRITICAL",
        Severity.MAJOR: "🟠 MAJOR",
        Severity.MINOR: "🟡 MINOR",
        Severity.COSMETIC: "⚪ COSMETIC",
    }.get(sev, str(sev.value))


def _wrap_html(md_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="utf-8"><title>TaxWatch Report</title>
<style>body{{font-family:sans-serif;max-width:900px;margin:0 auto;padding:2em;}}
pre{{background:#f5f5f5;padding:1em;overflow-x:auto;}}
details{{margin:0.5em 0;}}</style></head>
<body>
<pre>{md_content}</pre>
</body></html>"""
