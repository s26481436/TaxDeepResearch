"""Map connector keys to their normalizers."""

from __future__ import annotations

from taxwatch.normalize.base import Normalizer
from taxwatch.normalize.cn_tax_html import CnTaxHtmlNormalizer
from taxwatch.normalize.tw_law_json import TwLawJsonNormalizer
from taxwatch.normalize.tw_ruling_html import TwRulingHtmlNormalizer
from taxwatch.normalize.us_cfr_xml import UsCfrXmlNormalizer
from taxwatch.normalize.us_state_tax_html import UsStateTaxHtmlNormalizer

_NORMALIZER_MAP: dict[str, Normalizer] = {
    "tw_moj_law": TwLawJsonNormalizer(),
    "tw_mof_ruling": TwRulingHtmlNormalizer(),
    "tw_constitutional": TwRulingHtmlNormalizer(),
    "cn_chinatax": CnTaxHtmlNormalizer(),
    "cn_mof": CnTaxHtmlNormalizer(),
    "us_ecfr": UsCfrXmlNormalizer(),
    "us_govinfo_cfr": UsCfrXmlNormalizer(),
    "us_state_tax": UsStateTaxHtmlNormalizer(),
    "us_federal_register": TwRulingHtmlNormalizer(),  # FR docs are HTML articles
}


def get_normalizer(connector_key: str) -> Normalizer:
    if connector_key not in _NORMALIZER_MAP:
        raise ValueError(
            f"No normalizer for connector '{connector_key}'. "
            f"Available: {list(_NORMALIZER_MAP.keys())}"
        )
    return _NORMALIZER_MAP[connector_key]
