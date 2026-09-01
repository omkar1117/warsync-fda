#!/usr/bin/env python3
"""In-process walkthrough of the WarnSync serving layer — no transport, no server.

Prints the exact payloads the MCP tools return, then mutates the corpus to show
versioning, atomic visibility, withdrawal and point-in-time reads. This is the
script to run when you want copy-pasteable output for slides.

    python scripts/demo.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from warnsync_mcp.ingest import SyncEngine  # noqa: E402
from warnsync_mcp.models import iso  # noqa: E402
from warnsync_mcp.sources import JsonDirectorySource  # noqa: E402
from warnsync_mcp.store import VersionedStore  # noqa: E402


def show(title: str, payload: object) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("-" * len(title))
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:2600])


def _headline(text: str) -> str:
    """First line of substance — skips the synthetic banner and the salutation."""
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("[SYNTHETIC") and not line.startswith("Dear "):
            return line[:96]
    return text[:96]


def brief(title: str, hits: list) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("-" * len(title))
    for hit in hits:
        head = _headline(hit.text)
        print(f"  {hit.score:.3f}  {hit.letter_id} v{hit.version} c{hit.chunk_id}  {hit.recipient}")
        print(f"         {head}...")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="warnsync-demo-"))
    corpus = workdir / "corpus"
    shutil.copytree(ROOT / "data" / "sample_corpus", corpus)

    store = VersionedStore()
    engine = SyncEngine(store, JsonDirectorySource(corpus))

    print("=" * 72)
    print("WarnSync reference implementation — corpus:", corpus)
    print("=" * 72)

    # --- pass 1: cold start ------------------------------------------------
    events = engine.sync_once()
    show("Pass 1 — cold start (every letter is NEW)", {
        "events": [e.to_dict() for e in events],
        "sync_stats": engine.stats.to_dict(),
    })

    # --- pass 2: nothing changed ------------------------------------------
    t_before_idle = time.time()
    engine.sync_once()
    show("Pass 2 — idempotence (identical fingerprints, nothing re-embedded)", {
        "events_emitted": len(store.updates_since(t_before_idle)),
        "sync_stats": engine.stats.to_dict(),
    })

    # --- the four paper tools ---------------------------------------------
    brief(
        'search_letters("out-of-specification investigation root cause", k=3)',
        store.search("out-of-specification investigation root cause", k=3),
    )
    brief(
        'search_letters("audit trail deleted chromatography data", k=2)',
        store.search("audit trail deleted chromatography data", k=2),
    )
    show(
        'search_letters(... k=1) — a single result with full provenance',
        store.search("shared login administrator account", k=1)[0].to_dict(),
    )
    show(
        'violation_trends(provision="21 CFR 211")',
        store.trends("21 CFR 211"),
    )
    letter = store.get("WL-2025-0412")
    show("get_letter('WL-2025-0412') — metadata (text elided)", letter.manifest_row())

    # --- a letter is revised upstream --------------------------------------
    t_before_revision = time.time()
    target = corpus / "WL-2026-0119.json"
    record = json.loads(target.read_text(encoding="utf-8"))
    record["content"] += (
        "\n4. Your firm failed to establish an adequate written testing program "
        "designed to assess the stability characteristics of drug products "
        "(21 CFR 211.166(a)). Stability samples for product HCM-220 were stored "
        "outside the qualified chamber for eleven weeks.\n"
    )
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    events = engine.sync_once()
    show("Pass 3 — one letter revised upstream (MODIFIED, only that letter re-embedded)", {
        "events": [e.to_dict() for e in events],
        "sync_stats": engine.stats.to_dict(),
    })

    v1 = store.get("WL-2026-0119", 1)
    v2 = store.get("WL-2026-0119", 2)
    show("Both versions remain readable — an already-cited answer stays reproducible", {
        "v1": {"fingerprint": v1.fingerprint[:16], "chunks": len(v1.chunks),
               "citations": list(v1.cfr_citations), "ingested_at": iso(v1.ingested_at)},
        "v2": {"fingerprint": v2.fingerprint[:16], "chunks": len(v2.chunks),
               "citations": list(v2.cfr_citations), "ingested_at": iso(v2.ingested_at)},
        "current_alias": store.current("WL-2026-0119").version,
    })

    # --- a new letter is posted --------------------------------------------
    shutil.copy(ROOT / "data" / "incoming" / "WL-2026-0224.json", corpus)
    events = engine.sync_once()
    show("Pass 4 — a new letter is posted (NEW)", {
        "events": [e.to_dict() for e in events],
        "sync_stats": engine.stats.to_dict(),
    })
    brief(
        'The new letter is queryable immediately: search_letters("bioburden excursion reporting")',
        store.search("bioburden excursion deviation reporting biologics", k=2),
    )

    # --- a letter is withdrawn ---------------------------------------------
    (corpus / "WL-2025-0721.json").unlink()
    events = engine.sync_once()
    show("Pass 5 — a letter disappears upstream (REMOVED, recorded as a version)", {
        "events": [e.to_dict() for e in events],
        "withdrawn_letter": store.current("WL-2025-0721").manifest_row(),
        "still_readable_at_v1": store.get("WL-2025-0721", 1).status,
        "excluded_from_search": not any(
            hit.letter_id == "WL-2025-0721"
            for hit in store.search("dietary supplement identity testing", k=10)
        ),
    })

    # --- point in time ------------------------------------------------------
    show("Point-in-time read — the corpus as it stood before the revision", {
        "as_of": iso(t_before_revision),
        "letters_then": [
            f"{letter.letter_id} v{letter.version}" for letter in store.as_of(t_before_revision)
        ],
        "letters_now": [
            f"{letter.letter_id} v{letter.version}" for letter in store.current_letters()
        ],
    })

    show("list_updates(since=start) — the full update log", {
        "updates": [event.to_dict() for event in store.update_log],
    })
    show("corpus_status()", {"store": store.stats(), "sync": engine.stats.to_dict()})

    shutil.rmtree(workdir, ignore_errors=True)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
