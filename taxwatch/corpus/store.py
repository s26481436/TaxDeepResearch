"""Query the reference corpus: 文號 lookup, text search, official labels."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from taxwatch.models import CorpusDocument
from taxwatch.taxonomy import TaxType, by_key, classify
from taxwatch.wenhao import normalize


def lookup(
    session: Session,
    wenhao: str,
    *,
    corpus_key: str | None = None,
) -> CorpusDocument | None:
    """Find a corpus document by 文號. Returns None when absent."""
    key = normalize(wenhao)
    if not key:
        return None
    query = session.query(CorpusDocument).filter(CorpusDocument.document_number == key)
    if corpus_key:
        query = query.filter(CorpusDocument.corpus_key == corpus_key)
    return query.first()


def lookup_many(
    session: Session,
    wenhao_list: list[str],
    *,
    corpus_key: str | None = None,
) -> dict[str, CorpusDocument]:
    """Batch 文號 lookup — one query instead of N."""
    keys = [normalize(w) for w in wenhao_list if normalize(w)]
    if not keys:
        return {}
    query = session.query(CorpusDocument).filter(CorpusDocument.document_number.in_(keys))
    if corpus_key:
        query = query.filter(CorpusDocument.corpus_key == corpus_key)
    return {doc.document_number: doc for doc in query.all()}


def search(
    session: Session,
    query_text: str,
    *,
    limit: int = 5,
    corpus_key: str | None = None,
) -> list[CorpusDocument]:
    """Substring search over titles and content, titles first.

    Deliberately simple: at corpus scale (thousands of rows) a LIKE scan is
    fast enough, and it behaves identically on SQLite and PostgreSQL. Swap in
    a tsvector index here if a corpus ever grows past that.
    """
    term = (query_text or "").strip()
    if len(term) < 2:
        return []

    pattern = f"%{term}%"
    base = session.query(CorpusDocument)
    if corpus_key:
        base = base.filter(CorpusDocument.corpus_key == corpus_key)

    titles = base.filter(CorpusDocument.title.like(pattern)).limit(limit).all()
    if len(titles) >= limit:
        return titles

    seen = {d.id for d in titles}
    body = (
        base.filter(
            CorpusDocument.content.like(pattern),
            CorpusDocument.id.notin_(seen) if seen else True,
        )
        .limit(limit - len(titles))
        .all()
    )
    return titles + body


def classify_document(
    session: Session,
    title: str,
    document_number: str = "",
    *,
    country: str = "CN",
    corpus_key: str | None = None,
) -> TaxType:
    """Tax type for a document — the corpus's official label where we have it.

    The title heuristic measures ~69% against the corpus's own labels, so an
    exact 文號 match is always preferred. Falls back to the heuristic for
    documents the corpus does not cover.
    """
    if document_number:
        doc = lookup(session, document_number, corpus_key=corpus_key)
        if doc and doc.tax_keys:
            resolved = by_key(doc.tax_keys[0])
            if resolved:
                return resolved
    return classify(title, country=country)


def tax_key_index(session: Session, *, corpus_key: str | None = None) -> dict[str, str]:
    """{文號: tax_key} for every labelled corpus row.

    Built once and reused when classifying a whole list of documents, so a
    dashboard page does not issue one query per row.
    """
    query = session.query(CorpusDocument.document_number, CorpusDocument.tax_keys).filter(
        CorpusDocument.document_number != ""
    )
    if corpus_key:
        query = query.filter(CorpusDocument.corpus_key == corpus_key)
    return {num: keys[0] for num, keys in query.all() if keys}


def make_classifier(
    session: Session,
    *,
    corpus_key: str | None = None,
) -> Callable[[str, str, str], TaxType]:
    """Build a corpus-backed classifier with a single query.

    Callers that classify a whole list of documents (any dashboard page) use
    this instead of `classify_document`, which would issue one query per row.
    """
    try:
        index = tax_key_index(session, corpus_key=corpus_key)
    except Exception:  # noqa: BLE001 — corpus table may not exist yet
        index = {}

    def _classify(title: str, document_number: str = "", country: str = "CN") -> TaxType:
        key = index.get(normalize(document_number)) if document_number else None
        if key:
            resolved = by_key(key)
            if resolved:
                return resolved
        return classify(title, country=country)

    return _classify


def repealed_document_numbers(
    session: Session,
    *,
    corpus_key: str | None = None,
) -> dict[str, str]:
    """{文號: aging} for documents the corpus marks as dead.

    `aging` is the status at the corpus's crawl date, so callers must present
    it as "as of <corpus_version>" rather than as current truth.
    """
    query = session.query(CorpusDocument.document_number, CorpusDocument.aging).filter(
        CorpusDocument.document_number != "",
        or_(CorpusDocument.aging == "全文废止", CorpusDocument.aging == "全文失效"),
    )
    if corpus_key:
        query = query.filter(CorpusDocument.corpus_key == corpus_key)
    return dict(query.all())


def stats(session: Session) -> list[dict]:
    """Per-corpus summary for the settings page.

    Grouped by corpus_key alone: rows within one import can carry an empty
    corpus_version, and those must not appear as a separate corpus.
    """
    keys = [k for (k,) in session.query(CorpusDocument.corpus_key).distinct().all()]
    out = []
    for key in sorted(keys):
        base = session.query(CorpusDocument).filter_by(corpus_key=key)
        versions = sorted(
            {
                v
                for (v,) in session.query(CorpusDocument.corpus_version)
                .filter_by(corpus_key=key)
                .distinct()
                .all()
                if v
            }
        )
        out.append(
            {
                "corpus_key": key,
                "corpus_version": versions[-1] if versions else "",
                "documents": base.count(),
                "with_document_number": base.filter(CorpusDocument.document_number != "").count(),
                "repealed": base.filter(
                    or_(CorpusDocument.aging == "全文废止", CorpusDocument.aging == "全文失效")
                ).count(),
            }
        )
    return out
