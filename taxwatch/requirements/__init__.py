"""申報規範 — the per-tax-type baseline a filer must comply with.

Change detection answers "what moved". This layer answers the question a
finance team actually has: *given that, what do we now have to do*. It is the
matrix they otherwise keep in a spreadsheet — rate, tax base, formula,
deadlines, required documents — one row per (tax type, scenario, filer role).

The point of holding it here rather than in Excel is that every cell records
which provisions it was derived from. When change detection sees one of those
provisions move, that cell — and only that cell — is flagged for review.
"""

from taxwatch.requirements.fields import FIELD_SPECS, FieldSpec, field_spec

__all__ = ["FIELD_SPECS", "FieldSpec", "field_spec"]
