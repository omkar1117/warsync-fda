# Evaluation harness

Four conditions, one corpus, one store implementation, one embedder. What
differs between them is *when ingestion happens relative to the query*, so a
difference in the numbers is attributable to the update strategy.

| | Strategy |
|---|---|
| **B1** static | index once, never update — the staleness reference |
| **B2** full re-index | rebuild the whole index in place, periodically |
| **B3** synchronous | no background work; every query first brings the index up to date |
| **WS** WarnSync | asynchronous incremental sync |

```bash
python experiments/run.py --size 500 --cycles 10 --repeats 10 \
    --duration 20 --update-period 0.5 --qps 8
```

Results land in `results.json`. The published run is in the repository README.

## What each experiment measures

**E1 — ingestion efficiency.** Work required to absorb one upstream change (one
new letter, one revised letter). Reported as fraction of corpus reprocessed,
chunks embedded, and wall time.

**E2 — availability under load.** Query latency at a **fixed offered rate**
while updates run continuously on a fixed cadence. Both rates are pinned
deliberately; see the methodology notes below.

**E3 — time to queryable.** Elapsed time from an upstream change to that change
being visible to a query. Detection delay is *excluded*: it is bounded by the
poll interval, which is a configuration constant rather than a property of the
system.

**E4 — equivalence and stability.** Whether an index maintained by incremental
upserts converges to the index a clean rebuild produces, and how much answers
to unchanged questions move as the corpus grows.

## Methodology notes

These are the decisions that determine whether the numbers mean anything.

**Load is offered open-loop at a fixed rate, not by saturating the process.**
This is not a stylistic choice. The reference store searches by scanning every
chunk in pure Python, so a saturating query loop is CPU-bound and holds the
GIL. Measured that way, the background ingester is starved by roughly **60x** —
a `sync_once` that takes 68ms uncontended takes over 4 seconds against a
saturating reader. A closed-loop benchmark would then report excellent query
latency for WarnSync for precisely the wrong reason: because no ingestion
happened. At a fixed sub-saturation rate both threads run, and the question
becomes the intended one.

**The update cadence is pinned too.** Left to run as fast as it can, the update
thread's share of CPU varies by condition — a strategy that blocks readers
starves them and then completes *more* update cycles, inverting the comparison.
Every condition is offered the same change rate; a strategy that cannot keep up
records missed deadlines, which is itself a result.

**Detection delay is excluded from E3** for the reason given above. A total
freshness lag would be dominated by the poll interval, which would make the
number a restatement of a configuration constant.

**B2 models a naive in-place rebuild** — the baseline the design argues
against. A build-then-swap variant would avoid the downtime at the cost of
holding two indexes in memory. It would still pay the same 100% recompute that
E1 measures.

## What these numbers do not support

- **Nothing about retrieval quality.** The embedder is a hashed bag-of-words
  placeholder. No Recall@k or nDCG@k number from this harness would mean
  anything, and none is reported.
- **Nothing about a real corpus.** The corpus is generated. It is uniform in a
  way real enforcement letters are not — similar length, similar structure,
  citations drawn from a fixed pool.
- **Nothing about absolute performance.** The store is in-memory with a
  brute-force scan and no ANN index. Absolute latencies reflect that. The
  *ratios* between conditions are the result; the milliseconds are not.
- **Nothing about live freshness lag.** No live source is polled. Against a
  real listing, posting time is knowable only to the granularity the source
  publishes, so end-to-end lag is measurable to ±(poll interval) at best.

The wall-clock gap in E1 in particular **understates** what a real deployment
would see: with the placeholder embedder, embedding is so cheap that WarnSync's
per-cycle cost is dominated by the O(N) fingerprint scan rather than by
embedding. Compare chunks embedded instead — that ratio is the one a real
embedding model would expose.
