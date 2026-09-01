"""The versioned hybrid store: relational manifest + vector index + alias swap.

Concurrency model
-----------------
Writers stage a complete version under a lock and then flip a single
per-letter alias entry. `LetterVersion` and `Chunk` are frozen, and readers
take a snapshot of the alias map before touching anything, so a read observes
either version v or version v+1 of a given letter — never a mixture. That is
the whole of the "letter version read consistency" property; there is no
cross-letter transaction and none is claimed.

Superseded versions are tombstoned rather than deleted, which is what makes
point-in-time reads ("what did the corpus say on date d?") and per-answer
provenance possible.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .embedding import DEFAULT_DIM, HashingEmbedder, cosine, tokenize
from .models import ChangeEvent, ChangeKind, Chunk, LetterVersion, iso

#: Weight of dense similarity against lexical query coverage. Regulatory queries
#: lean on exact citation strings ("211.192"), which a small dense vector blurs,
#: so the lexical half carries real weight rather than being a tie-breaker.
DENSE_WEIGHT = 0.6


@dataclass(frozen=True)
class ManifestEntry:
    """What the change detector diffs against: one row per letter."""

    letter_id: str
    fingerprint: str
    version: int
    ingested_at: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "letter_id": self.letter_id,
            "fingerprint": self.fingerprint,
            "version": self.version,
            "ingested_at": iso(self.ingested_at),
            "status": self.status,
        }


@dataclass(frozen=True)
class SearchHit:
    """A retrieved chunk with the provenance an auditable answer needs."""

    letter_id: str
    version: int
    chunk_id: int
    score: float
    text: str
    recipient: str
    office: str
    issuance_date: str
    posting_date: str
    cfr_citations: tuple[str, ...]
    ingested_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "letter_id": self.letter_id,
            "version": self.version,
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "text": self.text,
            "provenance": {
                "recipient": self.recipient,
                "office": self.office,
                "issuance_date": self.issuance_date,
                "posting_date": self.posting_date,
                "cfr_citations": list(self.cfr_citations),
                "ingested_at": iso(self.ingested_at),
                "cite_as": f"{self.letter_id} v{self.version} chunk {self.chunk_id}",
            },
        }


class VersionedStore:
    """In-memory reference implementation of the WarnSync hybrid store.

    Swap the internals for Postgres + an ANN index in production; the method
    surface below is what the MCP server and the sync engine actually depend on.
    """

    def __init__(self, embedder: Callable[[str], tuple[float, ...]] | None = None) -> None:
        self._embed = embedder or HashingEmbedder(DEFAULT_DIM)
        self._lock = threading.RLock()
        self._versions: dict[tuple[str, int], LetterVersion] = {}
        self._alias: dict[str, int] = {}
        self._manifest: dict[str, ManifestEntry] = {}
        self._staged: dict[tuple[str, int], LetterVersion] = {}
        self._tombstoned: dict[tuple[str, int], float] = {}
        self._update_log: list[ChangeEvent] = []

    # ---------------------------------------------------------------- writes

    def next_version(self, letter_id: str) -> int:
        with self._lock:
            return self._alias.get(letter_id, 0) + 1

    def stage(self, version: LetterVersion) -> None:
        """Write a complete version into the store *without* making it visible."""
        with self._lock:
            self._staged[(version.letter_id, version.version)] = version

    def commit(self, letter_id: str, version: int, kind: ChangeKind) -> ChangeEvent:
        """Atomically publish a staged version and tombstone its predecessor.

        This is the only operation that changes what a query can see.
        """
        with self._lock:
            staged = self._staged.pop((letter_id, version), None)
            if staged is None:
                raise KeyError(f"no staged version {letter_id} v{version}")
            previous = self._alias.get(letter_id)
            self._versions[(letter_id, version)] = staged
            self._alias[letter_id] = version  # <- the atomic visibility swap
            if previous is not None:
                self._tombstoned[(letter_id, previous)] = staged.ingested_at
            self._manifest[letter_id] = ManifestEntry(
                letter_id=letter_id,
                fingerprint=staged.fingerprint,
                version=version,
                ingested_at=staged.ingested_at,
                status=staged.status,
            )
            event = ChangeEvent(letter_id=letter_id, version=version, kind=kind)
            self._update_log.append(event)
            return event

    # ---------------------------------------------------------------- reads

    def manifest(self) -> dict[str, ManifestEntry]:
        with self._lock:
            return dict(self._manifest)

    def current(self, letter_id: str) -> LetterVersion | None:
        """Read through the current-version alias — the default read surface."""
        with self._lock:
            version = self._alias.get(letter_id)
            if version is None:
                return None
            return self._versions.get((letter_id, version))

    def get(self, letter_id: str, version: int | None = None) -> LetterVersion | None:
        if version is None:
            return self.current(letter_id)
        with self._lock:
            return self._versions.get((letter_id, version))

    def versions_of(self, letter_id: str) -> list[LetterVersion]:
        with self._lock:
            found = [v for (lid, _), v in self._versions.items() if lid == letter_id]
        return sorted(found, key=lambda v: v.version)

    def current_letters(self, include_withdrawn: bool = False) -> list[LetterVersion]:
        with self._lock:
            alias = dict(self._alias)
            letters = [self._versions[(lid, v)] for lid, v in alias.items()]
        if not include_withdrawn:
            letters = [letter for letter in letters if letter.status == "active"]
        return sorted(letters, key=lambda letter: letter.letter_id)

    def as_of(self, timestamp: float, include_withdrawn: bool = False) -> list[LetterVersion]:
        """Point-in-time read: the corpus as it stood at `timestamp`.

        This is what turns the index into an audit log — an answer given last
        quarter can be reproduced against the corpus that produced it.
        """
        with self._lock:
            snapshot = list(self._versions.values())
        latest: dict[str, LetterVersion] = {}
        for version in snapshot:
            if version.ingested_at > timestamp:
                continue
            held = latest.get(version.letter_id)
            if held is None or version.version > held.version:
                latest[version.letter_id] = version
        letters = list(latest.values())
        if not include_withdrawn:
            letters = [letter for letter in letters if letter.status == "active"]
        return sorted(letters, key=lambda letter: letter.letter_id)

    def updates_since(self, since: float) -> list[ChangeEvent]:
        with self._lock:
            return [e for e in self._update_log if e.committed_at > since]

    @property
    def update_log(self) -> list[ChangeEvent]:
        with self._lock:
            return list(self._update_log)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            active = sum(1 for e in self._manifest.values() if e.status == "active")
            newest = max((e.ingested_at for e in self._manifest.values()), default=0.0)
            return {
                "letters": len(self._manifest),
                "active_letters": active,
                "withdrawn_letters": len(self._manifest) - active,
                "versions_retained": len(self._versions),
                "tombstoned_versions": len(self._tombstoned),
                "chunks_current": sum(
                    len(self._versions[(lid, v)].chunks) for lid, v in self._alias.items()
                ),
                "committed_changes": len(self._update_log),
                "last_commit": iso(newest) if newest else None,
            }

    # ---------------------------------------------------------------- search

    def search(
        self,
        query: str,
        k: int = 5,
        *,
        recipient: str | None = None,
        office: str | None = None,
        cfr: str | None = None,
        posted_after: str | None = None,
        posted_before: str | None = None,
        include_withdrawn: bool = False,
        as_of: float | None = None,
    ) -> list[SearchHit]:
        """Hybrid dense + lexical retrieval over the current-version alias.

        `as_of` reruns the same query against a past corpus state, which is how
        the evaluation replays a query against successive index states.
        """
        letters = (
            self.as_of(as_of, include_withdrawn)
            if as_of is not None
            else self.current_letters(include_withdrawn)
        )
        letters = [
            letter
            for letter in letters
            if _matches(letter, recipient, office, cfr, posted_after, posted_before)
        ]
        query_vec = self._embed(query)
        query_tokens = set(tokenize(query))
        hits: list[SearchHit] = []
        for letter in letters:
            for chunk in letter.chunks:
                dense = cosine(query_vec, chunk.embedding)
                lexical = _coverage(query_tokens, chunk.text)
                score = DENSE_WEIGHT * dense + (1.0 - DENSE_WEIGHT) * lexical
                if score <= 0.0:
                    continue
                hits.append(
                    SearchHit(
                        letter_id=letter.letter_id,
                        version=letter.version,
                        chunk_id=chunk.chunk_id,
                        score=score,
                        text=chunk.text,
                        recipient=letter.recipient,
                        office=letter.office,
                        issuance_date=letter.issuance_date,
                        posting_date=letter.posting_date,
                        cfr_citations=letter.cfr_citations,
                        ingested_at=letter.ingested_at,
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.letter_id, hit.chunk_id))
        return hits[:k]

    def trends(
        self,
        provision: str | None = None,
        *,
        window_start: str | None = None,
        window_end: str | None = None,
        top_recipients: int = 5,
    ) -> dict[str, Any]:
        """Count cited provisions across the current corpus, with top recipients.

        A provision is counted once per letter that cites it, not once per
        mention, so the number reads as "letters citing this provision".
        """
        letters = [
            letter
            for letter in self.current_letters()
            if _in_window(letter.issuance_date or letter.posting_date, window_start, window_end)
        ]
        counts: dict[str, int] = {}
        recipients: dict[str, list[str]] = {}
        by_year: dict[str, int] = {}
        matched: set[str] = set()
        for letter in letters:
            for citation in letter.cfr_citations:
                if provision and not citation.startswith(provision):
                    continue
                counts[citation] = counts.get(citation, 0) + 1
                recipients.setdefault(citation, []).append(letter.recipient)
                matched.add(letter.letter_id)
                year = (letter.issuance_date or letter.posting_date)[:4]
                if year:
                    by_year[year] = by_year.get(year, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return {
            "provision_filter": provision,
            "window": {"start": window_start, "end": window_end},
            "letters_in_window": len(letters),
            "letters_matching": len(matched),
            "provisions": [
                {
                    "provision": citation,
                    "letters_citing": count,
                    "top_recipients": _top(recipients[citation], top_recipients),
                }
                for citation, count in ranked
            ],
            "by_year": dict(sorted(by_year.items())),
        }


# -------------------------------------------------------------------- helpers


def _coverage(query_tokens: set[str], text: str) -> float:
    """Fraction of query tokens present in the chunk."""
    if not query_tokens:
        return 0.0
    chunk_tokens = set(tokenize(text))
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _matches(
    letter: LetterVersion,
    recipient: str | None,
    office: str | None,
    cfr: str | None,
    posted_after: str | None,
    posted_before: str | None,
) -> bool:
    if recipient and recipient.lower() not in letter.recipient.lower():
        return False
    if office and office.lower() not in letter.office.lower():
        return False
    if cfr and not any(c.startswith(cfr) for c in letter.cfr_citations):
        return False
    return _in_window(letter.posting_date, posted_after, posted_before)


def _in_window(date: str, start: str | None, end: str | None) -> bool:
    """ISO dates compare correctly as strings; missing dates never filter out."""
    if not date:
        return not (start or end)
    if start and date < start:
        return False
    if end and date > end:
        return False
    return True


def _top(values: Iterable[str], n: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]
    return [{"recipient": name, "letters": count} for name, count in ranked]
