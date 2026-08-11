"""The columns of the 申報規範 matrix, defined once.

Mirrors the spreadsheet finance already maintains, so an imported sheet, an
LLM extraction and the web form all agree on what each cell means.

`derivable` marks whether a cell can be traced back to provision text. Most can:
a rate, a deadline and a tax base are all written down somewhere. A few are
judgement — 「不適用特殊稅收優惠政策」 is a conclusion drawn from the *absence*
of a provision, and no amendment will ever announce it. Those are never
auto-flagged as stale, because a flag that cannot be resolved by reading the
diff just trains reviewers to dismiss flags.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_zh: str
    description: str
    derivable: bool = True
    # Rendered as a code/formula block rather than prose.
    monospace: bool = False


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "applicability",
        "適用條件",
        "此規範適用的門檻或前提，例如「年應稅銷售額 > 500 萬元」。"
        "若無門檻限制，寫明「無門檻限制」而非留白。",
    ),
    FieldSpec(
        "taxable_event",
        "課稅事件／觸發時點",
        "納稅義務發生的時點。列出一般情形與特殊情形（如租賃、勞務、先開發票的例外）。",
    ),
    FieldSpec(
        "rate",
        "法定稅率／徵收率／費率",
        "適用稅率。若分級或分項，逐項列出並註明適用範圍。",
    ),
    FieldSpec(
        "taxable_items",
        "應稅項目分類與說明",
        "此情境涵蓋的應稅標的。可列舉貨物或勞務類別。",
    ),
    FieldSpec(
        "formula",
        "應納稅額計算公式",
        "財務可直接套用的計算式，例如「銷項稅額 = 銷售額 × 13%」。多步驟時逐行列出。",
        monospace=True,
    ),
    FieldSpec(
        "tax_base",
        "稅基／課稅基礎",
        "計算稅額的基礎金額如何認定，含價外費用、含稅換算等。",
        monospace=True,
    ),
    FieldSpec(
        "deductions",
        "扣除／扣抵／抵免／不得扣抵",
        "可扣抵項目、憑證要求，以及明文不得扣抵的情形。",
    ),
    FieldSpec(
        "incentives",
        "租稅優惠／減免",
        "適用的優惠與其條件、期限。若確認無適用優惠，明確寫出。",
        # A statute rarely says "no incentive applies here"; that is inferred
        # from nothing matching, so no diff can confirm or refute it.
        derivable=False,
    ),
    FieldSpec(
        "filing_deadline",
        "申報期限",
        "納稅期間如何核定、申報週期、期滿後幾日內申報，含節假日順延規則。",
    ),
    FieldSpec(
        "payment_deadline",
        "繳款期限／開徵期間",
        "繳納期限、預繳與結算安排、進口貨物的特別規定、滯納金比例。",
    ),
    FieldSpec(
        "administration",
        "徵收管理",
        "申報對象與地點／平台、應備書表與憑證、應保存的文件。",
        # Which forms a bureau wants is administrative practice; the statute
        # says "向主管稅務機關申報" and stops there.
        derivable=False,
    ),
)

_BY_KEY = {spec.key: spec for spec in FIELD_SPECS}

FIELD_KEYS: tuple[str, ...] = tuple(spec.key for spec in FIELD_SPECS)

DERIVABLE_FIELD_KEYS: frozenset[str] = frozenset(spec.key for spec in FIELD_SPECS if spec.derivable)


def field_spec(key: str) -> FieldSpec | None:
    return _BY_KEY.get(key)


def label(key: str) -> str:
    spec = _BY_KEY.get(key)
    return spec.label_zh if spec else key
