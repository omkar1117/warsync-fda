"""Core immutable data types shared by the store, sync engine and server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

#: A letter is NEW the first time it is seen, MODIFIED when its content
#: fingerprint changes, and REMOVED when it disappears from the upstream listing
#: (the agency occasionally revises or withdraws letters).
ChangeKind = Literal["NEW", "MODIFIED", "REMOVED"]

LetterStatus = Literal["active", "withdrawn"]


@dataclass(frozen=True)
class SourceRecord:
    """One letter as observed upstream, before ingestion.

    A ``CorpusSource`` yields these. Only ``letter_id`` and ``content`` are
    required; anything a particular source already knows (because the upstream
    listing exposes it) can be filled in, and whatever is left blank is the
    ``MetadataExtractor``'s job.
    """

    letter_id: str
    content: str
    posting_date: str = ""
    issuance_date: str = ""
    recipient: str = ""
    office: str = ""
    subject: str = ""
    cfr_citations: tuple[str, ...] = ()
    source_url: str = ""
    synthetic: bool = False


@dataclass(frozen=True)
class Chunk:
    """One embedded retrieval unit, keyed by (letter_id, version, chunk_id)."""

    letter_id: str
    version: int
    chunk_id: int
    text: str
    embedding: tuple[float, ...]

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.letter_id, self.version, self.chunk_id)


@dataclass(frozen=True)
class LetterVersion:
    """An immutable snapshot of one letter at one version.

    Versions are never mutated in place. A new ingestion stages v+1 and the
    store flips the letter's current-version alias, so a reader sees either v or
    v+1 and never a torn letter.
    """

    letter_id: str
    version: int
    fingerprint: str
    text: str
    status: LetterStatus = "active"
    posting_date: str = ""
    issuance_date: str = ""
    recipient: str = ""
    office: str = ""
    subject: str = ""
    cfr_citations: tuple[str, ...] = ()
    source_url: str = ""
    synthetic: bool = False
    ingested_at: float = field(default_factory=time.time)
    chunks: tuple[Chunk, ...] = ()

    def manifest_row(self) -> dict[str, Any]:
        """The relational manifest row for this version (Sec. III-D of the paper)."""
        return {
            "letter_id": self.letter_id,
            "version": self.version,
            "issuance_date": self.issuance_date,
            "posting_date": self.posting_date,
            "ingested_at": iso(self.ingested_at),
            "recipient": self.recipient,
            "office": self.office,
            "subject": self.subject,
            "cfr_citations": list(self.cfr_citations),
            "status": self.status,
            "fingerprint": self.fingerprint,
            "chunks": len(self.chunks),
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class ChangeEvent:
    """Emitted on every committed change; the unit of the update log."""

    letter_id: str
    version: int
    kind: ChangeKind
    committed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "letter_id": self.letter_id,
            "version": self.version,
            "kind": self.kind,
            "committed_at": iso(self.committed_at),
        }


def iso(epoch: float) -> str:
    """UTC ISO-8601 timestamp, second resolution — used in every wire payload."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
