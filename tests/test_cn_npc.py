"""The cn_npc connector must not turn a broken upstream into a clean run.

Reported symptom: `taxwatch run --source cn-npc` reported success having
stored nothing, so the 母法 never appeared and there was no error to explain
why. Every search had failed with 405 and been swallowed.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from taxwatch.connectors.base import ConnectorError
from taxwatch.connectors.cn_npc import CnNpcConnector

_SEARCH = "https://flk.npc.gov.cn/api/"


def _connector() -> CnNpcConnector:
    return CnNpcConnector({"keywords": ["增值税法", "企业所得税法"]})


@respx.mock
def test_every_search_failing_raises_instead_of_returning_empty():
    respx.post(_SEARCH).mock(return_value=httpx.Response(405, text="Not Allowed"))

    with pytest.raises(ConnectorError) as exc:
        _connector().discover()

    # The message has to name the failure, or the operator is no better off.
    assert "405" in str(exc.value) or "HTTPStatusError" in str(exc.value)


@respx.mock
def test_a_genuinely_empty_result_is_not_an_error():
    """No matches is a fact about the source, not a malfunction."""
    respx.post(_SEARCH).mock(return_value=httpx.Response(200, json={"result": {"data": []}}))

    assert _connector().discover() == []


@respx.mock
def test_partial_failure_keeps_what_succeeded():
    payload = {
        "result": {
            "data": [
                {
                    "id": "abc123",
                    "title": "中华人民共和国增值税法",
                    "publish": "2024-12-25 00:00:00",
                    "office": "全国人民代表大会常务委员会",
                }
            ]
        }
    }
    responses = [
        httpx.Response(200, json=payload),
        httpx.Response(500, text="boom"),
    ]
    respx.post(_SEARCH).mock(side_effect=responses)

    refs = _connector().discover()

    assert [r.title for r in refs] == ["中华人民共和国增值税法"]
