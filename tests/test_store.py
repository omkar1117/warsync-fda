"""Versioning, visibility, filters and point-in-time reads."""

import time

import pytest

from warnsync_mcp.embedding import HashingEmbedder
from warnsync_mcp.models import Chunk, LetterVersion
from warnsync_mcp.store import VersionedStore

EMBED = HashingEmbedder()


def make_version(letter_id, version, text, *, status="active", ingested_at=None, **meta):
    return LetterVersion(
        letter_id=letter_id,
        version=version,
        fingerprint=f"fp-{letter_id}-{version}",
        text=text,
        status=status,
        ingested_at=ingested_at if ingested_at is not None else time.time(),
        chunks=(Chunk(letter_id, version, 0, text, EMBED(text)),),
        **meta,
    )


@pytest.fixture
def store():
    store = VersionedStore(EMBED)
    store.stage(make_version(
        "L1", 1, "sterility investigation was not thorough",
        recipient="Acme Sterile", office="CDER", posting_date="2025-01-10",
        issuance_date="2025-01-02", cfr_citations=("21 CFR 211.192",),
    ))
    store.commit("L1", 1, "NEW")
    store.stage(make_version(
        "L2", 1, "audit trail disabled on chromatography workstation",
        recipient="Beta Labs", office="CDER", posting_date="2025-02-10",
        issuance_date="2025-02-01", cfr_citations=("21 CFR 211.68(b)",),
    ))
    store.commit("L2", 1, "NEW")
    return store


def test_staged_versions_are_invisible_until_committed(store):
    store.stage(make_version("L3", 1, "not yet visible"))
    assert store.current("L3") is None
    assert "L3" not in store.manifest()
    store.commit("L3", 1, "NEW")
    assert store.current("L3").version == 1


def test_commit_requires_a_staged_version(store):
    with pytest.raises(KeyError):
        store.commit("L1", 99, "MODIFIED")


def test_alias_flips_to_the_new_version_and_the_old_stays_readable(store):
    store.stage(make_version("L1", 2, "sterility investigation was revised"))
    store.commit("L1", 2, "MODIFIED")
    assert store.current("L1").version == 2
    assert store.get("L1", 1).text == "sterility investigation was not thorough"
    assert [v.version for v in store.versions_of("L1")] == [1, 2]


def test_next_version_tracks_the_alias(store):
    assert store.next_version("L1") == 2
    assert store.next_version("unknown") == 1


def test_search_returns_provenance(store):
    hits = store.search("audit trail disabled", k=1)
    assert hits[0].letter_id == "L2"
    payload = hits[0].to_dict()
    assert payload["provenance"]["recipient"] == "Beta Labs"
    assert payload["provenance"]["cite_as"] == "L2 v1 chunk 0"


def test_search_reads_only_the_current_version(store):
    store.stage(make_version("L1", 2, "replaced entirely with unrelated wording"))
    store.commit("L1", 2, "MODIFIED")
    hits = store.search("sterility investigation thorough", k=10)
    assert all(hit.version == 2 for hit in hits if hit.letter_id == "L1")


def test_search_filters(store):
    assert [h.letter_id for h in store.search("investigation", k=5, recipient="Acme")] == ["L1"]
    assert [h.letter_id for h in store.search("audit", k=5, cfr="21 CFR 211.68")] == ["L2"]
    recent = store.search("audit trail", k=5, posted_after="2025-02-01")
    assert [h.letter_id for h in recent] == ["L2"]
    assert store.search("audit trail", k=5, posted_before="2025-01-31") == []


def test_withdrawn_letters_are_hidden_but_still_readable(store):
    store.stage(make_version("L2", 2, "audit trail disabled on chromatography workstation",
                             status="withdrawn"))
    store.commit("L2", 2, "REMOVED")
    assert [h.letter_id for h in store.search("audit trail", k=5)] == []
    assert [h.letter_id for h in store.search("audit trail", k=5, include_withdrawn=True)] == ["L2"]
    assert store.get("L2", 1).status == "active"


def test_as_of_returns_the_corpus_at_a_past_instant(store):
    checkpoint = time.time()
    time.sleep(0.01)
    store.stage(make_version("L3", 1, "a letter posted after the checkpoint"))
    store.commit("L3", 1, "NEW")
    assert [letter.letter_id for letter in store.as_of(checkpoint)] == ["L1", "L2"]
    assert [letter.letter_id for letter in store.current_letters()] == ["L1", "L2", "L3"]


def test_as_of_search_ignores_later_versions(store):
    checkpoint = time.time()
    time.sleep(0.01)
    store.stage(make_version("L1", 2, "completely different wording about labeling"))
    store.commit("L1", 2, "MODIFIED")
    old = store.search("sterility investigation", k=5, as_of=checkpoint)
    assert old and all(hit.version == 1 for hit in old if hit.letter_id == "L1")


def test_updates_since_windows_the_log(store):
    checkpoint = time.time()
    time.sleep(0.01)
    store.stage(make_version("L3", 1, "new arrival"))
    store.commit("L3", 1, "NEW")
    events = store.updates_since(checkpoint)
    assert [(e.letter_id, e.kind) for e in events] == [("L3", "NEW")]
    assert len(store.update_log) == 3


def test_trends_counts_letters_not_mentions(store):
    store.stage(make_version("L3", 1, "another sterility failure", issuance_date="2025-03-01",
                             recipient="Gamma Pharma", cfr_citations=("21 CFR 211.192",)))
    store.commit("L3", 1, "NEW")
    result = store.trends("21 CFR 211.192")
    assert result["provisions"][0]["provision"] == "21 CFR 211.192"
    assert result["provisions"][0]["letters_citing"] == 2
    assert {r["recipient"] for r in result["provisions"][0]["top_recipients"]} == {
        "Acme Sterile", "Gamma Pharma"
    }


def test_trends_honours_the_window(store):
    assert store.trends(window_start="2025-02-01")["letters_in_window"] == 1
    assert store.trends(window_end="2024-12-31")["letters_in_window"] == 0


def test_stats_track_tombstones(store):
    store.stage(make_version("L1", 2, "revised"))
    store.commit("L1", 2, "MODIFIED")
    stats = store.stats()
    assert stats["letters"] == 2
    assert stats["versions_retained"] == 3
    assert stats["tombstoned_versions"] == 1
