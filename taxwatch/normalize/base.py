from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from taxwatch.connectors.base import RawDocument


@dataclass
class ProvisionData:
    """A single provision (條文) extracted from a document."""

    node_key: str
    heading: str
    text: str


@dataclass
class NormalizedDoc:
    """Normalized representation of a legal document."""

    external_id: str
    title: str
    provisions: list[ProvisionData]
    metadata: dict


class Normalizer(ABC):
    @abstractmethod
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        """Convert raw document content into structured provisions."""
