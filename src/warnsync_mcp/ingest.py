"""The sync engine: fingerprint change detection and incremental ingestion.

One pass of `sync_once()` is Algorithm 1 of the paper: fetch the upstream
listing, fingerprint each letter, classify it NEW / MODIFIED / REMOVED against
the manifest, and for each change stage version v+1, flip the alias, and emit a
change event. Unchanged letters are never re-embedded, and re-running a pass on
unchanged content is a no-op — the idempotence the design requires.

Nothing here touches the query path. The engine runs on a background thread (or
a background task) and the serving plane reads through the alias the whole time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .embedding import DEFAULT_DIM, HashingEmbedder, chunk_text
from .models import ChangeEvent, ChangeKind, Chunk, LetterVersion, SourceRecord
from .sources import CorpusSource, RegexMetadataExtractor, fingerprint
from .store import VersionedStore

ChangeListener = Callable[[ChangeEvent], None]


@dataclass
class SyncStats:
    """Per-pass accounting — this is what the ingestion-efficiency metric reads."""

    passes: int = 0
    letters_seen: int = 0
    unchanged: int = 0
    new: int = 0
    modified: int = 0
    removed: int = 0
    chunks_embedded: int = 0
    last_pass_seconds: float = 0.0

    @property
    def reprocessed_fraction(self) -> float:
        """Fraction of the observed corpus that the last pass had to re-embed."""
        if not self.letters_seen:
            return 0.0
        return (self.new + self.modified) / self.letters_seen

    def to_dict(self) -> dict[str, object]:
        return {
            "passes": self.passes,
            "letters_seen": self.letters_seen,
            "unchanged": self.unchanged,
            "new": self.new,
            "modified": self.modified,
            "removed": self.removed,
            "chunks_embedded": self.chunks_embedded,
            "reprocessed_fraction": round(self.reprocessed_fraction, 4),
            "last_pass_seconds": round(self.last_pass_seconds, 4),
        }


class SyncEngine:
    """Drives change detection and ingestion against a `VersionedStore`."""

    def __init__(
        self,
        store: VersionedStore,
        source: CorpusSource,
        *,
        embedder: Callable[[str], tuple[float, ...]] | None = None,
        extractor: Callable[[SourceRecord], SourceRecord] | None = None,
        target_tokens: int = 512,
        overlap_tokens: int = 48,
    ) -> None:
        self.store = store
        self.source = source
        self.embed = embedder or HashingEmbedder(DEFAULT_DIM)
        self.extract = extractor or RegexMetadataExtractor()
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        self.stats = SyncStats()
        self._listeners: list[ChangeListener] = []

    def add_listener(self, listener: ChangeListener) -> None:
        """Register a commit-and-notify hook (the MCP server registers one)."""
        self._listeners.append(listener)

    # ------------------------------------------------------------ detection

    def detect(self, records: Sequence[SourceRecord]) -> list[tuple[str, ChangeKind]]:
        """Diff observed fingerprints against the manifest (Algorithm 1, lines 3-13)."""
        manifest = self.store.manifest()
        changes: list[tuple[str, ChangeKind]] = []
        seen: set[str] = set()
        for record in records:
            seen.add(record.letter_id)
            entry = manifest.get(record.letter_id)
            if entry is None:
                changes.append((record.letter_id, "NEW"))
            elif entry.fingerprint != fingerprint(record.content):
                changes.append((record.letter_id, "MODIFIED"))
            elif entry.status == "withdrawn":
                # Reappeared upstream after a withdrawal: republish it.
                changes.append((record.letter_id, "MODIFIED"))
        for letter_id, entry in manifest.items():
            if letter_id not in seen and entry.status != "withdrawn":
                changes.append((letter_id, "REMOVED"))
        return changes

    # ------------------------------------------------------------ ingestion

    def ingest(self, record: SourceRecord, kind: ChangeKind) -> ChangeEvent:
        """Parse, chunk, embed and stage v+1, then commit it atomically."""
        enriched = self.extract(record)
        version = self.store.next_version(record.letter_id)
        texts = chunk_text(enriched.content, self.target_tokens, self.overlap_tokens)
        chunks = tuple(
            Chunk(
                letter_id=enriched.letter_id,
                version=version,
                chunk_id=index,
                text=text,
                embedding=self.embed(text),
            )
            for index, text in enumerate(texts)
        )
        self.stats.chunks_embedded += len(chunks)
        self.store.stage(
            LetterVersion(
                letter_id=enriched.letter_id,
                version=version,
                fingerprint=fingerprint(enriched.content),
                text=enriched.content,
                status="active",
                posting_date=enriched.posting_date,
                issuance_date=enriched.issuance_date,
                recipient=enriched.recipient,
                office=enriched.office,
                subject=enriched.subject,
                cfr_citations=tuple(enriched.cfr_citations),
                source_url=enriched.source_url,
                synthetic=enriched.synthetic,
                ingested_at=time.time(),
                chunks=chunks,
            )
        )
        return self.store.commit(enriched.letter_id, version, kind)

    def withdraw(self, letter_id: str) -> ChangeEvent | None:
        """Record a withdrawal as a new version rather than a deletion.

        The letter stays readable at its prior version — a withdrawal is itself
        a fact the corpus has to be able to state, and deleting the rows would
        make an already-cited answer unreproducible.
        """
        current = self.store.current(letter_id)
        if current is None or current.status == "withdrawn":
            return None
        version = self.store.next_version(letter_id)
        withdrawn = LetterVersion(
            letter_id=current.letter_id,
            version=version,
            fingerprint=current.fingerprint,
            text=current.text,
            status="withdrawn",
            posting_date=current.posting_date,
            issuance_date=current.issuance_date,
            recipient=current.recipient,
            office=current.office,
            subject=current.subject,
            cfr_citations=current.cfr_citations,
            source_url=current.source_url,
            synthetic=current.synthetic,
            ingested_at=time.time(),
            chunks=current.chunks,
        )
        self.store.stage(withdrawn)
        return self.store.commit(letter_id, version, "REMOVED")

    # ------------------------------------------------------------ the loop

    def sync_once(self) -> list[ChangeEvent]:
        """One full poll-diff-ingest-commit-notify pass. Safe to call repeatedly."""
        started = time.time()
        records = list(self.source.fetch())
        by_id = {record.letter_id: record for record in records}
        changes = self.detect(records)

        self.stats.passes += 1
        self.stats.letters_seen = len(records)
        self.stats.unchanged = len(records) - sum(1 for _, k in changes if k != "REMOVED")
        self.stats.new = sum(1 for _, k in changes if k == "NEW")
        self.stats.modified = sum(1 for _, k in changes if k == "MODIFIED")
        self.stats.removed = sum(1 for _, k in changes if k == "REMOVED")

        events: list[ChangeEvent] = []
        for letter_id, kind in changes:
            event = (
                self.withdraw(letter_id)
                if kind == "REMOVED"
                else self.ingest(by_id[letter_id], kind)
            )
            if event is not None:
                events.append(event)
                self._emit(event)
        self.stats.last_pass_seconds = time.time() - started
        return events

    def _emit(self, event: ChangeEvent) -> None:
        """Listeners are advisory: a failing one must not stall ingestion."""
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - notification delivery is best-effort
                continue
