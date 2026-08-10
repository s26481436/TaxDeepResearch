from __future__ import annotations

from taxwatch.connectors.base import Connector

_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    _REGISTRY[cls.key] = cls
    return cls


def get_connector(key: str, config: dict) -> Connector:
    if key not in _REGISTRY:
        raise ValueError(f"Unknown connector: {key}. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[key](config)


def _load_builtins():
    from taxwatch.connectors.tw_constitutional import TwConstitutionalConnector
    from taxwatch.connectors.tw_mof_ruling import TwMofRulingConnector
    from taxwatch.connectors.tw_moj_law import TwMojLawConnector
    from taxwatch.connectors.us_federal_register import UsFederalRegisterConnector

    for cls in [
        TwMojLawConnector, TwMofRulingConnector,
        TwConstitutionalConnector, UsFederalRegisterConnector,
    ]:
        register(cls)


_load_builtins()
