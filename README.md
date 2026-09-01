# WarnSync MCP

A reference implementation of the **serving layer** from *WarnSync: An
Asynchronously Synchronized Model Context Protocol Server for FDA
Warning-Letter Intelligence* — an MCP server that keeps a stable tool surface
over a document corpus that keeps changing underneath it.

The problem it addresses: most RAG systems index once and serve forever. For a
regulatory enforcement corpus that is wrong in a way that matters — new letters
are posted continuously, old ones are revised and occasionally withdrawn, and an
agent answering from a stale index is not merely unhelpful but misleading. The
answer here is not to retrain anything. The model stays frozen; the corpus is
versioned; every retrieved chunk carries the letter version and ingestion
timestamp that produced it; and clients learn the corpus moved through the
protocol itself rather than by polling or convention.

```
$ python scripts/demo_live.py

5. A new letter appears upstream
  dropped WL-2026-0224.json into the watched corpus at 18:31:14
  (the server is polling; nothing was told to it directly)

6. Waiting for the protocol-native notification...
  + 1.95s  ResourceUpdated  uri=warnsync://manifest
  + 1.95s  ResourcesListChanged

7. Refetching what changed (notify-then-fetch)
  NEW      WL-2026-0224 v1 at 2026-08-31T23:31:33Z

8. The same query, after the commit
  WL-2026-0224 v1 c0 (0.445) — Stonebrook Biologics, Inc.
```

## Scope — what this repository is and is not

**Included:** the versioned store, the change-detection and ingestion logic, the
MCP server, and a synthetic corpus that makes all of it runnable offline.

**Not included:** the production corpus pipeline — the live FDA listing poller,
letter HTML retrieval and OCR for scanned PDFs, the neural embedding model, the
learned metadata extractor, and the evaluation harness and its results. Those
are part of the research prototype and stay with the paper.

That split is deliberate and the seams are explicit, not vestigial. The parts
that are absent sit behind three protocols — `CorpusSource`, `Embedder` and
`MetadataExtractor` — each with a working default here, so what is published is
a complete, testable system rather than a sketch with holes in it. Point
`CorpusSource.fetch()` at a real listing and nothing else changes.

## Install and run

Requires Python 3.10+. The only runtime dependency is the MCP SDK — everything
else (chunking, embedding, the store, retrieval) is standard library, so the
tests and demos run with no model download and no network access.

```bash
python3 -m venv .venv && source .venv/bin/activate

pip install -e ".[dev]"        # or: pip install -r requirements-dev.txt

python scripts/demo.py        # in-process walkthrough; prints every tool payload
python scripts/demo_live.py   # spawns the server, subscribes, watches a change land
pytest                        # 40 tests, including 4 over a real stdio connection

python -m warnsync_mcp --corpus data/sample_corpus --poll-interval 30
```

To connect a client, see [examples/](examples/). Captured output from both
demos is in [docs/sample-output.md](docs/sample-output.md), and
[docs/roadmap.md](docs/roadmap.md) covers what the missing pieces would need to
look like to compose with what is here.

## Tool surface

The tools never change. Synchronization alters the data behind them, never their
shape — the inverse of a design that keeps the corpus fixed and mutates the tool
catalog.

| Tool | Arguments | Returns |
|---|---|---|
| `search_letters` | `query`, `k`, filters (`recipient`, `office`, `cfr`, date range), `as_of` | top-k chunks, each with full provenance |
| `get_letter` | `letter_id`, `version` | full text, metadata, every known version |
| `violation_trends` | `provision` prefix, date window | letters citing each provision, top recipients |
| `list_updates` | `since` | committed changes since a timestamp |
| `corpus_status` | — | corpus size, retained versions, last commit, ingestion cost |

The first four are the paper's Table I. `corpus_status` is an operational
addition — it is what makes freshness observable in a demo.

Resources: `warnsync://manifest` (one row per letter — subscribe here) and
`warnsync://letter/{letter_id}`.

## Results

Four conditions, one corpus, one store, one embedder — differing only in *when
ingestion happens relative to the query*. Corpus of 500 generated letters, 10 update cycles,
run on Python 3.13.7 / macOS arm64.
Reproduce with `python experiments/run.py`; raw output is in
[experiments/results.json](experiments/results.json).

| | B1 static | B2 full re-index | B3 synchronous | **WS (WarnSync)** |
|---|---|---|---|---|
| Corpus reprocessed per update | 0.0% | 99.1% | 0.4% | **0.4%** |
| Chunks embedded per update | 0.0 | 728.4 | 2.7 | **2.7** |
| Time to queryable (median) | never | 389.8 ms | 185.1 ms | **113.3 ms** |
| Sustained throughput (8 qps offered) | 8.05 | 2.90 | 5.40 | **8.05** |
| Query latency p50 | 72.2 ms | 459.7 ms | 187.2 ms | **76.3 ms** |
| Query latency p95 | 75.4 ms | 488.3 ms | 195.2 ms | **132.4 ms** |
| Background update deadlines missed | n/a — never updates | 20 / 41 | n/a — updates on query path | **1 / 41** |
| Serves fresh content | no | yes | yes | **yes** |

**B1 is the ceiling, not a competitor.** It does no work and answers from a
frozen index, so its latency is the best any condition could achieve. The
result that matters is that **WarnSync matches it** — 8.05 qps against
B1's 8.05, p50 76.3 ms against 72.2 ms — while applying every
update B1 ignores, missing 1 of 41 deadlines.

**The two alternatives to asynchrony both cost the query path.** Rebuilding in
place (B2) drops sustained throughput to 2.90 qps — 2.8x fewer queries served —
and raises p50 latency 6.4x, while still missing 20 of 41 update deadlines.
Putting ingestion on the query path (B3) makes every query pay for it:
p50 rises 2.6x.

**Incremental updating is sound, not just fast.** After
10 update cycles, the incrementally-maintained index returns
**identical** top-10 results to a clean rebuild from the same corpus state
for all 12 of 12 evaluation queries (Jaccard
1.00, Kendall τ 1.00). It does not drift.
Answers to unchanged questions stay stable as the corpus grows
(mean Jaccard@10 0.994 between consecutive index states).

### What these numbers do not show

Stated plainly, because the gap between them and a retrieval-quality claim is
where this kind of result usually gets oversold:

- **No retrieval quality.** The embedder is a hashed bag-of-words placeholder.
  Recall@k and nDCG@k are not reported because they would be meaningless.
- **No real corpus.** The letters are generated and uniform in ways real
  enforcement letters are not.
- **No live freshness lag.** Nothing polls a real listing. Detection delay is
  excluded from "time to queryable" precisely because it is bounded by the poll
  interval — a configuration constant, not a property of the system.
- **Ratios, not milliseconds.** The store is in-memory with a brute-force
  scan. Absolute latencies reflect that; only the comparisons are the result.

One caveat cuts *against* WarnSync's numbers and is worth stating: with a
placeholder embedder, embedding is so cheap that WarnSync's per-cycle cost is
dominated by the O(N) fingerprint scan rather than by embedding. The wall-clock
gap therefore **understates** what a real embedding model would show — compare
chunks embedded (728.4 vs 2.7, a
270x difference) for the ratio a real deployment would expose.

Methodology, including why load is offered open-loop and why the update cadence
is pinned, is documented in [experiments/README.md](experiments/README.md).

## How it works

```
  ┌─ background ingestion plane ─────────────────────────────┐
  │  source.fetch()  →  SHA-256 diff  →  parse · chunk ·     │
  │                     vs manifest      embed  →  stage v+1 │
  └──────────────────────────────┬───────────────────────────┘
                                 │ atomic alias swap + change event
                    ┌────────────▼────────────┐
                    │  versioned hybrid store │
                    │  manifest + vectors     │
                    │  tombstoned versions    │
                    └────────────┬────────────┘
                                 │ reads through the current-version alias
  ┌──────────────────────────────▼───────────────────────────┐
  │  MCP server: 5 tools, 2 resources                        │
  │  ──── ResourceUpdated / ResourcesListChanged ───→ agent  │
  └──────────────────────────────────────────────────────────┘
```

Ingestion latency is never on the query path. A pass fetches the corpus,
fingerprints each document against the manifest, and processes only what
changed. Each changed document is staged in full as version *v+1* and made
visible by flipping a single alias entry, so a concurrent read sees either *v*
or *v+1* and never a half-written document. The previous version is tombstoned
rather than deleted.

Five properties fall out of that, and each is covered by tests:

- **Freshness** — a new document is queryable one poll interval plus one
  ingestion pass after it appears. `test_server_stdio.py` measures this over a
  real connection.
- **Availability** — queries are served throughout ingestion. There is no
  rebuild step and no downtime.
- **Auditability** — every hit carries `letter_id`, `version`, `chunk_id` and
  `ingested_at`. Superseded versions stay readable, and `as_of` replays a query
  against the corpus as it stood at any past instant, so an answer given last
  quarter can be reproduced against the corpus that produced it.
- **Protocol-native change signaling** — commits publish `ResourceUpdated` for
  the manifest, plus `ResourcesListChanged` when documents are added or
  withdrawn. Events carry no payload; a client learns only that something moved
  and refetches what it depends on, which is why a duplicated or dropped event
  costs a refetch and nothing worse.
- **Incremental, idempotent ingestion** — unchanged content produces an
  identical fingerprint and is skipped. Re-running a pass is a no-op.

A withdrawal is recorded as a new version with `status: withdrawn`, not as a
deletion: it disappears from search but stays readable at its prior version.
A corpus that cannot state that a document was withdrawn cannot support an audit.

## Extending it

```python
from warnsync_mcp import SyncEngine, VersionedStore
from warnsync_mcp.models import SourceRecord
from warnsync_mcp.server import build_server

class MyListingSource:
    def fetch(self) -> list[SourceRecord]:
        ...  # poll your listing, return one record per document

store = VersionedStore(embedder=my_embedder)          # any text -> vector callable
engine = SyncEngine(store, MyListingSource(), embedder=my_embedder)
handle = build_server(store, engine, poll_interval=300)
```

The store is in-memory by design — it is a reference implementation, and the
method surface is small enough that backing it with Postgres plus an ANN index
is a contained change. The bundled `HashingEmbedder` is a deterministic hashed
bag-of-words vectorizer with no model download, so the tests and demos run
anywhere; replace it with a real embedding model before drawing conclusions
about retrieval quality.

## Sample corpus

Every bundled letter is **synthetic** — invented firms, dates and findings,
each marked `"synthetic": true` and banner-tagged in its own text. Nothing here
reproduces a real FDA warning letter. They are written in the register of real
enforcement correspondence and cite real CFR provisions because that is what the
citation extractor, the chunker and the retriever need in order to be exercised
meaningfully. See [data/README.md](data/README.md).

## A note on the protocol revision

Subscription streams here use `subscriptions/listen` (SEP-2575), the mechanism
in the 2026-07-28 MCP revision. The paper describes the same notify-then-fetch
pattern in the older `resources/subscribe` +
`notifications/resources/updated` vocabulary. The wire spelling changed; the
design argument did not. Clients on earlier revisions use `subscribe_resource()`
and receive change notifications through `message_handler`.

## Repository layout

```
src/warnsync_mcp/
  models.py      immutable data types
  embedding.py   tokenizing, chunking, the placeholder embedder
  sources.py     CorpusSource / MetadataExtractor + fingerprinting
  store.py       versioned store: alias swap, tombstones, hybrid search
  ingest.py      change detection and incremental ingestion
  server.py      MCP tools, resources, commit-and-notify
scripts/         two runnable demos
tests/           unit tests + stdio integration tests
data/            synthetic corpus
```

## License

MIT — see [LICENSE](LICENSE).
