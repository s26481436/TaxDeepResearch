from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class ConnectorError(RuntimeError):
    """Raised when a connector cannot do its job at all.

    Distinct from "the source genuinely has nothing new": a connector that
    returns an empty list is reporting a fact, while one that raises this is
    reporting that it never got to look.
    """


@dataclass
class DocumentRef:
    external_id: str
    title: str
    doc_type: str
    url: str = ""
    issued_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RawDocument:
    external_id: str
    content: bytes
    content_type: str = "text/html"
    url: str = ""
    metadata: dict = field(default_factory=dict)


class Connector(ABC):
    key: str
    country: str

    def __init__(self, source_config: dict):
        self.source_config = source_config

    @abstractmethod
    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        """List documents that should be tracked."""

    @abstractmethod
    def fetch(self, ref: DocumentRef) -> RawDocument:
        """Fetch raw content for a document."""
