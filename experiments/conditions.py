"""The four conditions under comparison.

All four share one store implementation, one embedder and one corpus, so a
difference between them is attributable to the update strategy and to nothing
else. They differ only in *when* ingestion happens relative to the query.

  B1  static      index once, never update            — the staleness reference
  B2  rebuild     periodic full re-index, in place    — availability + compute cost
  B3  synchronous ingest on the query path            — freshness bought with latency
  WS  WarnSync    asynchronous incremental sync       — the proposed design

B2 models a *naive* full re-index: the index is rebuilt in place, so queries
cannot be served while it runs. That is the baseline the design argues against.
A build-then-swap variant would avoid the downtime at the cost of holding two
indexes in memory; it would still pay the same 100% recompute, which is what
Experiment 1 measures.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from warnsync_mcp.embedding import HashingEmbedder
from warnsync_mcp.ingest import SyncEngine
from warnsync_mcp.sources import JsonDirectorySource
from warnsync_mcp.store import SearchHit, VersionedStore


@dataclass
class UpdateStats:
    """What one update opportunity cost."""

    seconds: float = 0.0
    letters_reprocessed: int = 0
    chunks_embedded: int = 0
    letters_seen: int = 0
    committed: int = 0

    @property
    def fraction_reprocessed(self) -> float:
        return self.letters_reprocessed / self.letters_seen if self.letters_seen else 0.0


class Condition:
    """Base: an initial build, an update policy, and a query path."""

    name = "base"
    label = "base"

    def __init__(self, corpus: Path, embedder=None) -> None:
        self.corpus = corpus
        self.embed = embedder or HashingEmbedder()
        self.store = VersionedStore(self.embed)
        self.engine = SyncEngine(self.store, JsonDirectorySource(corpus), embedder=self.embed)
        #: Held exclusively by a condition that cannot serve during an update.
        self._serving = threading.Lock()

    def initial_build(self) -> UpdateStats:
        return self._sync()

    def update(self) -> UpdateStats:
        """One sync opportunity — what the background loop would do on a tick."""
        raise NotImplementedError

    def search(self, query: str, k: int = 10) -> list[SearchHit]:
        with self._serving:
            return self.store.search(query, k=k)

    def absorb_change(self) -> UpdateStats:
        """Do whatever this strategy does in response to an upstream change.

        For three of the four conditions that is `update()`. B3 overrides it,
        because its work happens on the query path — charging that cost to the
        update would hide exactly the property the condition exists to expose.
        """
        return self.update()

    def _sync(self) -> UpdateStats:
        started = time.perf_counter()
        before = self.engine.stats.chunks_embedded
        events = self.engine.sync_once()
        stats = self.engine.stats
        return UpdateStats(
            seconds=time.perf_counter() - started,
            letters_reprocessed=stats.new + stats.modified + stats.removed,
            chunks_embedded=stats.chunks_embedded - before,
            letters_seen=stats.letters_seen,
            committed=len(events),
        )


class StaticIndex(Condition):
    """B1 — index once and never update. Always available, increasingly wrong."""

    name = "B1"
    label = "B1 static"

    def update(self) -> UpdateStats:
        # Does not even poll: the point of B1 is that no work is done at all.
        return UpdateStats(seconds=0.0, letters_seen=len(self.store.manifest()))


class FullRebuild(Condition):
    """B2 — discard the index and rebuild it from scratch, in place."""

    name = "B2"
    label = "B2 full re-index"

    def update(self) -> UpdateStats:
        started = time.perf_counter()
        with self._serving:  # the service is down for the duration of the rebuild
            store = VersionedStore(self.embed)
            engine = SyncEngine(store, JsonDirectorySource(self.corpus), embedder=self.embed)
            events = engine.sync_once()
            self.store, self.engine = store, engine
        return UpdateStats(
            seconds=time.perf_counter() - started,
            letters_reprocessed=engine.stats.letters_seen,
            chunks_embedded=engine.stats.chunks_embedded,
            letters_seen=engine.stats.letters_seen,
            committed=len(events),
        )


class SynchronousIngest(Condition):
    """B3 — no background work; every query first brings the index up to date."""

    name = "B3"
    label = "B3 synchronous"

    def update(self) -> UpdateStats:
        # No background work; everything happens in search().
        return UpdateStats(seconds=0.0, letters_seen=len(self.store.manifest()))

    def search(self, query: str, k: int = 10) -> list[SearchHit]:
        with self._serving:
            self.engine.sync_once()  # freshness, paid for on the query path
            return self.store.search(query, k=k)

    def absorb_change(self) -> UpdateStats:
        """B3 ingests nothing until a query arrives, so charge the next query."""
        started = time.perf_counter()
        before = self.engine.stats.chunks_embedded
        with self._serving:
            events = self.engine.sync_once()
            self.store.search("ingestion charged to this query", k=1)
        stats = self.engine.stats
        return UpdateStats(
            seconds=time.perf_counter() - started,
            letters_reprocessed=stats.new + stats.modified + stats.removed,
            chunks_embedded=stats.chunks_embedded - before,
            letters_seen=stats.letters_seen,
            committed=len(events),
        )


class WarnSync(Condition):
    """WS — asynchronous incremental sync; queries never wait for ingestion."""

    name = "WS"
    label = "WS (WarnSync)"

    def update(self) -> UpdateStats:
        return self._sync()


ALL = [StaticIndex, FullRebuild, SynchronousIngest, WarnSync]
