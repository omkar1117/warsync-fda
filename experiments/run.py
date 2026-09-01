#!/usr/bin/env python3
"""Run the experiment suite and emit results as JSON and Markdown.

    python experiments/run.py [--size 1000] [--cycles 10] [--repeats 3]

Every experiment is a controlled replay against a generated corpus: the corpus
is deterministic, the mutations are scripted, and the four conditions see the
identical sequence of changes. What is being compared is the update strategy.

Read experiments/README.md for what each number does and does not support.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments import corpus as corpus_gen  # noqa: E402
from experiments import queries as query_set  # noqa: E402
from experiments.conditions import ALL, Condition, FullRebuild, WarnSync  # noqa: E402
from experiments.metrics import jaccard, kendall_tau, median, percentile  # noqa: E402
from warnsync_mcp.ingest import SyncEngine  # noqa: E402
from warnsync_mcp.sources import JsonDirectorySource  # noqa: E402
from warnsync_mcp.store import VersionedStore  # noqa: E402


# --------------------------------------------------------------------- helpers


def fresh_corpus(size: int) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="warnsync-exp-")) / "corpus"
    corpus_gen.generate(directory, size)
    return directory


def mutate(directory: Path, size: int, cycle: int) -> None:
    """One upstream change: a new letter arrives and an existing one is revised."""
    corpus_gen.add_letter(directory, size + cycle)
    corpus_gen.revise_letter(directory, cycle % size, revision=cycle + 1)


def hit_keys(hits) -> list[str]:
    return [f"{hit.letter_id}:{hit.chunk_id}" for hit in hits]


# ------------------------------------------------------- E1 ingestion efficiency


def experiment_ingestion(size: int, cycles: int) -> dict:
    """How much of the corpus each strategy reprocesses to absorb one change."""
    rows = []
    for factory in ALL:
        directory = fresh_corpus(size)
        condition = factory(directory)
        condition.initial_build()

        per_cycle = []
        for cycle in range(cycles):
            mutate(directory, size, cycle)
            per_cycle.append(condition.absorb_change())

        seen = max((s.letters_seen for s in per_cycle), default=size) or size
        reprocessed = statistics.mean(s.letters_reprocessed for s in per_cycle)
        rows.append({
            "condition": condition.name,
            "label": condition.label,
            "letters_reprocessed_per_cycle": reprocessed,
            "chunks_embedded_per_cycle": statistics.mean(
                s.chunks_embedded for s in per_cycle),
            "corpus_reprocessed_pct": 100.0 * reprocessed / seen,
            "seconds_per_cycle": statistics.mean(s.seconds for s in per_cycle),
            "work_on_query_path": condition.name == "B3",
        })
        shutil.rmtree(directory.parent, ignore_errors=True)
    return {"corpus_size": size, "cycles": cycles, "rows": rows}


# --------------------------------------------- E2 availability under query load


@dataclass
class Sample:
    start: float
    end: float
    ok: bool

    @property
    def latency_ms(self) -> float:
        return (self.end - self.start) * 1000.0


def experiment_availability(
    size: int, duration: float, update_period: float, query_rate: float
) -> dict:
    """Query latency at a fixed offered load, while updates run continuously.

    Load is generated open-loop at a fixed rate rather than by saturating the
    process, for a reason that is not cosmetic. This reference implementation
    searches by scanning every chunk in pure Python, so a saturating query loop
    is CPU-bound and holds the GIL; measured directly, that starves the
    background ingester by a factor of ~60x and the update thread never runs.
    A closed-loop benchmark would then report excellent query latency for
    exactly the wrong reason — because no ingestion happened.

    At a fixed sub-saturation rate both threads get to run, and the question
    the experiment asks is the right one: with updates landing continuously,
    what does a client's latency look like?
    """
    rows = []
    for factory in ALL:
        directory = fresh_corpus(size)
        condition = factory(directory)
        condition.initial_build()

        samples: list[Sample] = []
        updates, behind, late = [0], [0], [0]
        stop = threading.Event()

        def query_load() -> None:
            index = 0
            deadline = time.perf_counter()
            while not stop.is_set():
                deadline += 1.0 / query_rate
                query = query_set.BASE[index % len(query_set.BASE)]
                index += 1
                started = time.perf_counter()
                try:
                    condition.search(query, 10)
                    ok = True
                except Exception:  # noqa: BLE001 - a failed query is the measurement
                    ok = False
                samples.append(Sample(started, time.perf_counter(), ok))
                slack = deadline - time.perf_counter()
                if slack > 0:
                    time.sleep(slack)
                else:
                    late[0] += 1  # could not sustain the offered rate

        def update_load() -> None:
            cycle = 0
            deadline = time.perf_counter()
            while not stop.is_set():
                deadline += update_period
                mutate(directory, size, cycle)
                cycle += 1
                condition.update()
                updates[0] = cycle
                slack = deadline - time.perf_counter()
                if slack > 0:
                    time.sleep(slack)
                else:
                    behind[0] += 1

        readers = threading.Thread(target=query_load, daemon=True)
        writers = threading.Thread(target=update_load, daemon=True)
        readers.start()
        writers.start()
        time.sleep(duration)
        stop.set()
        readers.join(timeout=120)
        writers.join(timeout=120)

        latencies = [s.latency_ms for s in samples]
        rows.append({
            "condition": condition.name,
            "label": condition.label,
            "duration_s": duration,
            "offered_qps": query_rate,
            "queries_completed": len(samples),
            "achieved_qps": len(samples) / duration if duration else None,
            "queries_late": late[0],
            "success_rate_pct": 100.0 * sum(s.ok for s in samples) / len(samples)
            if samples else None,
            "p50_ms": median(latencies) if latencies else None,
            "p95_ms": percentile(latencies, 95) if latencies else None,
            "max_ms": max(latencies) if latencies else None,
            "updates_applied": updates[0],
            "update_deadlines_missed": behind[0],
            "update_period_s": update_period,
        })
        shutil.rmtree(directory.parent, ignore_errors=True)
    return {
        "corpus_size": size,
        "duration_s": duration,
        "offered_qps": query_rate,
        "update_period_s": update_period,
        "rows": rows,
    }


# ------------------------------------------------------------- E3 time to fresh


def experiment_freshness(size: int, repeats: int) -> dict:
    """Time from an upstream change to that change being queryable.

    Detection delay is excluded: it is bounded by the poll interval, which is a
    configuration constant rather than a property of the system. What is
    measured here is everything the system itself controls — diff, parse, chunk,
    embed, stage and commit.
    """
    rows = []
    for factory in ALL:
        directory = fresh_corpus(size)
        condition = factory(directory)
        condition.initial_build()

        latencies: list[float] = []
        queryable = []
        for cycle in range(repeats):
            new_id = corpus_gen.add_letter(directory, size + 1000 + cycle)
            started = time.perf_counter()
            condition.absorb_change()
            latencies.append((time.perf_counter() - started) * 1000.0)
            queryable.append(condition.store.current(new_id) is not None)

        rows.append({
            "condition": condition.name,
            "label": condition.label,
            "becomes_queryable": all(queryable),
            "median_ms": median(latencies) if latencies else None,
            "p95_ms": percentile(latencies, 95) if latencies else None,
        })
        shutil.rmtree(directory.parent, ignore_errors=True)
    return {"corpus_size": size, "repeats": repeats, "rows": rows}


# ---------------------------------------- E4 equivalence and retrieval stability


def experiment_equivalence(size: int, cycles: int, k: int = 10) -> dict:
    """Does incremental updating converge to what a full rebuild would produce?

    The concern this addresses: an index maintained by in-place incremental
    upserts could drift away from the index a clean rebuild produces — stale
    vectors, orphaned chunks, a missed alias flip. If it drifts, incremental
    ingestion is unsound however fast it is.
    """
    directory = fresh_corpus(size)
    incremental = WarnSync(directory)
    incremental.initial_build()

    states: list[dict[str, list[str]]] = [
        {query: hit_keys(incremental.search(query, k)) for query in query_set.BASE}
    ]
    for cycle in range(cycles):
        mutate(directory, size, cycle)
        incremental.update()
        states.append({query: hit_keys(incremental.search(query, k)) for query in query_set.BASE})

    # What a rebuild from the same final corpus produces.
    rebuilt_store = VersionedStore(incremental.embed)
    SyncEngine(rebuilt_store, JsonDirectorySource(directory),
               embedder=incremental.embed).sync_once()

    equal, jaccards, taus = 0, [], []
    for query in query_set.BASE:
        incr = hit_keys(incremental.search(query, k))
        full = hit_keys(rebuilt_store.search(query, k=k))
        equal += incr == full
        jaccards.append(jaccard(incr, full))
        taus.append(kendall_tau(incr, full))

    # Stability of base-query answers across successive index states.
    step_jaccard = [
        jaccard(states[i][query], states[i + 1][query])
        for i in range(len(states) - 1)
        for query in query_set.BASE
    ]
    drift = [
        jaccard(states[0][query], states[-1][query]) for query in query_set.BASE
    ]

    shutil.rmtree(directory.parent, ignore_errors=True)
    return {
        "corpus_size": size,
        "cycles": cycles,
        "k": k,
        "queries": len(query_set.BASE),
        "identical_result_lists": equal,
        "mean_jaccard_vs_rebuild": statistics.mean(jaccards),
        "mean_kendall_tau_vs_rebuild": statistics.mean(
            t for t in taus if t == t) if any(t == t for t in taus) else None,
        "mean_jaccard_between_states": statistics.mean(step_jaccard),
        "min_jaccard_between_states": min(step_jaccard),
        "mean_jaccard_first_to_last": statistics.mean(drift),
    }


# ---------------------------------------------------------------------- driver


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the WarnSync experiment suite.")
    parser.add_argument("--size", type=int, default=1000, help="corpus size in letters")
    parser.add_argument("--cycles", type=int, default=10, help="update cycles per condition")
    parser.add_argument("--repeats", type=int, default=5, help="repeats for freshness timing")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="seconds of sustained query load per condition")
    parser.add_argument("--update-period", type=float, default=0.5,
                        help="seconds between background update cycles")
    parser.add_argument("--qps", type=float, default=8.0,
                        help="offered query rate during the availability experiment")
    parser.add_argument("--out", type=Path, default=ROOT / "experiments" / "results.json")
    args = parser.parse_args()

    started = time.time()
    print(f"corpus size {args.size}, {args.cycles} update cycles", file=sys.stderr)

    print("E1 ingestion efficiency...", file=sys.stderr)
    e1 = experiment_ingestion(args.size, args.cycles)
    print("E2 availability under load...", file=sys.stderr)
    e2 = experiment_availability(args.size, args.duration, args.update_period, args.qps)
    print("E3 time to queryable...", file=sys.stderr)
    e3 = experiment_freshness(args.size, args.repeats)
    print("E4 incremental vs rebuild equivalence...", file=sys.stderr)
    e4 = experiment_equivalence(args.size, args.cycles)

    results = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "embedder": "HashingEmbedder(dim=256) — placeholder, not a neural model",
            "corpus": "generated synthetic, deterministic (seed 20260831)",
            "run_seconds": None,
        },
        "ingestion_efficiency": e1,
        "availability": e2,
        "freshness": e3,
        "equivalence": e4,
    }
    results["environment"]["run_seconds"] = round(time.time() - started, 1)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out} in {results['environment']['run_seconds']}s", file=sys.stderr)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
