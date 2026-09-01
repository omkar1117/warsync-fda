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
demos is in [docs/sample-output.md](docs/sample-output.md).

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

## Citation

If you use this code, please cite the paper:

```bibtex
@misc{pakki2026warnsync,
  author = {Pakki, Omkar},
  title  = {WarnSync: An Asynchronously Synchronized Model Context Protocol
            Server for FDA Warning-Letter Intelligence},
  year   = {2026}
}
```

## License

MIT — see [LICENSE](LICENSE).
