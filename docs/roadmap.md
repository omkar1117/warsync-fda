# Roadmap: from reference implementation to measured system

This document describes what stands between the current code and an evaluation
that can produce defensible numbers. It is written for anyone — including
future me — who picks this up and asks "what would I have to build?"

The [README](../README.md) states what is and is not included. This document
takes the next step and says, for the parts that are missing, *what shape they
need to be* so that they compose with what already exists.

## The state of things

What exists is the serving plane: a versioned store with atomic per-letter
visibility swaps, fingerprint-based change detection, incremental idempotent
ingestion, an MCP server with a stable tool surface, and protocol-native change
notification. It is tested, including over a real stdio connection.

What that buys is **mechanism**. The properties the design claims — queries
served throughout ingestion, no torn reads across a version boundary,
reprocessing unchanged content is free, a client learns of change through the
protocol, a past corpus state is reproducible — are properties of the
architecture and are demonstrated by the test suite.

What it does not buy is **measurement**. No freshness lag against a real
upstream, no comparison against alternative ingestion strategies, no retrieval
quality. Those need the four changes below, and they need them *before* any
evaluation is run, because each one is cheap now and expensive to retrofit
into a system that has already produced numbers.

## Four changes required before evaluation

### 1. Injectable clock

**Problem.** [`models.py`](../src/warnsync_mcp/models.py) reads the wall clock
through `default_factory=time.time` on `LetterVersion.ingested_at` and
`ChangeEvent.committed_at`. A temporal-slice protocol replays months of corpus
arrivals in minutes; with a wall clock every replayed version is stamped with
the time of the replay, not the time being simulated.

The failure mode is the dangerous kind: nothing raises, the numbers look
plausible, and every freshness measurement is wrong.

**Shape.**

```python
class Clock(Protocol):
    def now(self) -> float: ...

class SystemClock:
    def now(self) -> float:
        return time.time()

class ReplayClock:
    """Advances only when the harness advances it."""
    def __init__(self, start: float) -> None:
        self._t = start
    def now(self) -> float:
        return self._t
    def advance(self, seconds: float) -> None:
        self._t += seconds
```

Inject into `VersionedStore` and `SyncEngine`. Critically, **remove the
`default_factory` rather than defaulting it to `SystemClock`**: make the
timestamp a required constructor argument. A caller that forgets it should fail
loudly at construction, not silently produce wall-clock-stamped data inside a
replay.

The production server passes `SystemClock()`; the harness passes `ReplayClock`
and drives `sync_once()` directly instead of waiting on `_poll_forever`.

### 2. Full freshness-lag instrumentation

**Problem.** The lag decomposition the evaluation calls for has four
boundaries — posted, detected, committed, notified. The system currently
records two: `ingested_at` and `committed_at`. Detection time is never stored,
and notification time is not recorded at all, because notification happens in
[`server.py`](../src/warnsync_mcp/server.py) after the event has left the
engine. Neither can be reconstructed after the fact.

**Shape.** Widen `ChangeEvent`:

```python
@dataclass(frozen=True)
class ChangeEvent:
    letter_id: str
    version: int
    kind: ChangeKind
    posted_at: float | None   # upstream appearance; None when unknown
    detected_at: float        # the poll pass that observed the change
    committed_at: float       # the alias swap — the point of queryability
    notified_at: float | None # stamped by the server after publish
```

From which:

| Quantity | Expression |
|---|---|
| freshness lag | `committed_at - posted_at` |
| detection delay | `detected_at - posted_at` (bounded above by the poll interval) |
| processing latency | `committed_at - detected_at` |
| notification lag | `notified_at - committed_at` |

**A measurement limitation worth stating explicitly rather than discovering
later.** Against a live upstream, `posted_at` is only knowable to the
granularity the source publishes — typically a date, not a timestamp. With poll
interval Δ, the true posting time is only bounded to `[detected_at - Δ,
detected_at]`. Freshness lag against a live source is therefore measurable to
±Δ *at best*, and to ±1 day if the source publishes only dates.

This is a strong argument for making the **controlled replay the primary
measurement** — where `posted_at` is known exactly by construction — and
treating a live run as a sanity check on detection delay rather than as the
source of the headline number. Reporting a median freshness lag to the second
from a live scrape would be false precision, and a careful reviewer will say so.

### 3. A baseline seam

**Problem.** The evaluation compares four conditions. Only one of them exists.
`SyncEngine.sync_once()` hard-codes the asynchronous-incremental strategy;
there is no way to express "index once and stop", "rebuild the whole index
periodically", or "ingest synchronously on the query path".

Implementing the other three as separate scripts would mean comparing four
codebases rather than four strategies, and the difference would show up in the
numbers as noise that cannot be attributed.

**Shape.** Extract the policy, keep everything else shared:

```python
class IngestionStrategy(Protocol):
    name: str
    def on_tick(self, now: float) -> list[ChangeEvent]: ...
    def on_query(self, query: str) -> None: ...
```

| Condition | `on_tick` | `on_query` | What it isolates |
|---|---|---|---|
| B1 static | ingest on the first tick only | no-op | staleness with no synchronization |
| B2 full re-index | rebuild a fresh store, swap wholesale | no-op | availability and compute cost of rebuilding |
| B3 synchronous | no-op | sync before serving | latency when freshness sits on the query path |
| WS | incremental async sync | no-op | the proposed design |

All four share one store implementation, one metrics sink, and one corpus
replay, so a difference between conditions is attributable to the strategy and
nothing else.

B2 needs a wholesale swap. Rather than adding a `replace_all()` to the store,
let the harness hold the store *reference* and swap it — simpler, and it makes
B2's downtime window explicit and measurable instead of hiding it inside a
store method.

### 4. Persistence

**Problem.** The store is in-memory. A replay spanning a real collection window
cannot be paused, resumed, or inspected after the run, and a crash discards the
experiment. Runs also are not archivable, so a reviewer cannot re-derive a
table from the artifact.

**Shape.** SQLite, plus a memmapped array for vectors. Not Postgres and
pgvector — for a single-author evaluation, SQLite keeps the whole run
self-contained in a file a reviewer can download, which is worth more than
scalability nobody will exercise.

```sql
letter_versions(letter_id, version, fingerprint, status, posting_date,
                issuance_date, recipient, office, subject, source_url,
                ingested_at, text)          -- PK (letter_id, version)
citations(letter_id, version, provision)    -- violation_trends becomes a query
chunks(letter_id, version, chunk_id, text, vector_offset)
alias(letter_id, version)                   -- one row per letter
update_log(seq, letter_id, version, kind,
           posted_at, detected_at, committed_at, notified_at)
```

The atomic alias swap becomes `BEGIN; UPDATE alias; INSERT update_log; COMMIT`.
This is a strict improvement on the current mutex: the same atomicity, and it
survives a restart.

Note that `citations` as its own table turns `violation_trends` from a full
corpus scan into an indexed query, which matters once the corpus is real.

### A scaling note, not a blocker

`VersionedStore.as_of()` scans every retained version on every call. That is
fine at demo scale and will not be fine during evaluation, because the
retrieval-stability metric calls it once per query per index state. Index
versions per letter as a sorted `(ingested_at, version)` list and bisect.
Worth doing at the same time as persistence, since the SQLite schema gives it
to you for free with an index on `(letter_id, ingested_at)`.

## Build order

The ordering is driven by what unblocks what, not by what is most interesting.

1. **Embedder adapter.** Smallest change — the seam already exists. Unlocks
   nothing on its own, but everything downstream depends on it, and doing it
   first means the harness is never written against placeholder semantics.
2. **Clock, instrumentation, baseline seam.** Together, because they touch the
   same types. This is what makes freshness and availability measurable.
3. **Persistence and the `as_of` index.** Makes runs resumable and archivable.
4. **Replay harness.** Corpus slicing, strategy driver, metrics collection,
   table generation.
5. **Real corpus source.** Last, deliberately.

**On sequencing the real source last:** the poller is the riskiest component —
upstream structure changes, retrieval of scanned documents, rate limiting, and
parser breakage that arrives on someone else's schedule. It is also the one
most likely to consume a month. Building it before the harness that consumes it
means discovering harness requirements after the expensive part is already
written. Build the thing that has opinions about the data first.

When you do build it: verify what the source actually offers before writing a
scraper — a bulk download or structured endpoint may exist and would remove
most of the risk. Check terms of service and `robots.txt`, rate-limit
conservatively, and treat parser breakage as an expected operating condition
with an alarm on it rather than an exception. The existing design already
accommodates this: a failed pass only lengthens the current lag.

## What stays

The synthetic corpus stays after real data arrives. It is deterministic,
offline, needs no network, and is what keeps the test suite fast and
reproducible. It is the regression fixture, not a placeholder to be deleted.

The `HashingEmbedder` stays for the same reason. Tests that assert on retrieval
*mechanics* — that the current version is read, that a withdrawn letter is
excluded, that filters apply — should keep using it, because it makes them
deterministic and dependency-free. Only the evaluation needs a real model.
