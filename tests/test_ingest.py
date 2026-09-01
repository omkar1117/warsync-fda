"""Change detection, idempotence, versioning and withdrawal through the engine."""

import json
import shutil
from pathlib import Path

import pytest

from warnsync_mcp.ingest import SyncEngine
from warnsync_mcp.sources import JsonDirectorySource
from warnsync_mcp.store import VersionedStore

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_corpus"
INCOMING = Path(__file__).resolve().parents[1] / "data" / "incoming"


@pytest.fixture
def corpus(tmp_path):
    target = tmp_path / "corpus"
    shutil.copytree(SAMPLE, target)
    return target


@pytest.fixture
def engine(corpus):
    store = VersionedStore()
    return SyncEngine(store, JsonDirectorySource(corpus))


def edit(path: Path, extra: str) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    record["content"] += extra
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def test_cold_start_ingests_every_letter_as_new(engine):
    events = engine.sync_once()
    assert len(events) == len(list(SAMPLE.glob("*.json")))
    assert {event.kind for event in events} == {"NEW"}
    assert all(event.version == 1 for event in events)
    assert engine.stats.reprocessed_fraction == 1.0


def test_second_pass_is_a_no_op(engine):
    engine.sync_once()
    embedded = engine.stats.chunks_embedded
    assert engine.sync_once() == []
    assert engine.stats.chunks_embedded == embedded
    assert engine.stats.reprocessed_fraction == 0.0


def test_modified_letter_gets_a_new_version_only_for_itself(engine, corpus):
    engine.sync_once()
    embedded = engine.stats.chunks_embedded
    edit(corpus / "WL-2025-0412.json", "\n\nAdditional finding under 21 CFR 211.166(a).\n")

    events = engine.sync_once()
    assert [(e.letter_id, e.kind, e.version) for e in events] == [
        ("WL-2025-0412", "MODIFIED", 2)
    ]
    assert engine.stats.modified == 1
    assert engine.stats.new == 0
    assert engine.stats.chunks_embedded > embedded
    assert engine.store.current("WL-2025-0412").version == 2
    assert engine.store.get("WL-2025-0412", 1) is not None


def test_new_citations_are_picked_up_on_revision(engine, corpus):
    engine.sync_once()
    before = engine.store.current("WL-2025-0412").cfr_citations
    edit(corpus / "WL-2025-0412.json", "\n\nAlso cites 21 CFR 211.166(a).\n")
    engine.sync_once()
    after = engine.store.current("WL-2025-0412").cfr_citations
    assert "21 CFR 211.166(a)" in after
    assert "21 CFR 211.166(a)" not in before


def test_new_letter_is_detected_and_immediately_queryable(engine, corpus):
    engine.sync_once()
    assert all(
        hit.letter_id != "WL-2026-0224"
        for hit in engine.store.search("bioburden excursion biologics", k=10)
    )

    shutil.copy(INCOMING / "WL-2026-0224.json", corpus)
    events = engine.sync_once()
    assert [(e.letter_id, e.kind) for e in events] == [("WL-2026-0224", "NEW")]

    hits = engine.store.search("bioburden excursion deviation reporting", k=1)
    assert hits[0].letter_id == "WL-2026-0224"


def test_removal_is_recorded_as_a_version_not_a_deletion(engine, corpus):
    engine.sync_once()
    (corpus / "WL-2025-0721.json").unlink()

    events = engine.sync_once()
    assert [(e.letter_id, e.kind, e.version) for e in events] == [
        ("WL-2025-0721", "REMOVED", 2)
    ]
    assert engine.store.current("WL-2025-0721").status == "withdrawn"
    assert engine.store.get("WL-2025-0721", 1).status == "active"
    assert all(
        hit.letter_id != "WL-2025-0721"
        for hit in engine.store.search("dietary supplement identity", k=10)
    )


def test_removal_is_not_re_emitted_on_later_passes(engine, corpus):
    engine.sync_once()
    (corpus / "WL-2025-0721.json").unlink()
    engine.sync_once()
    assert engine.sync_once() == []


def test_a_withdrawn_letter_that_reappears_is_republished(engine, corpus):
    engine.sync_once()
    saved = (corpus / "WL-2025-0721.json").read_bytes()
    (corpus / "WL-2025-0721.json").unlink()
    engine.sync_once()

    (corpus / "WL-2025-0721.json").write_bytes(saved)
    events = engine.sync_once()
    assert [(e.letter_id, e.kind, e.version) for e in events] == [
        ("WL-2025-0721", "MODIFIED", 3)
    ]
    assert engine.store.current("WL-2025-0721").status == "active"


def test_listeners_receive_every_committed_change(engine, corpus):
    seen = []
    engine.add_listener(seen.append)
    engine.sync_once()
    assert len(seen) == len(list(corpus.glob("*.json")))


def test_a_raising_listener_does_not_stall_ingestion(engine):
    def explode(_event):
        raise RuntimeError("notification transport is down")

    seen = []
    engine.add_listener(explode)
    engine.add_listener(seen.append)
    events = engine.sync_once()
    assert len(events) == len(seen)
