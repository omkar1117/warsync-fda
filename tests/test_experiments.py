"""Smoke tests for the evaluation harness.

These do not assert on measured values — timings are machine-dependent and
asserting on them would produce a flaky suite. They assert on the properties
that must hold for the published numbers to mean anything: that the conditions
differ in the way they are claimed to, and that incremental ingestion converges
to what a rebuild produces.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments import corpus as corpus_gen  # noqa: E402
from experiments.conditions import ALL, FullRebuild, StaticIndex, WarnSync  # noqa: E402
from experiments.metrics import jaccard, kendall_tau, percentile  # noqa: E402
from experiments.run import experiment_equivalence, experiment_ingestion  # noqa: E402

SIZE = 40


@pytest.fixture
def corpus(tmp_path):
    return corpus_gen.generate(tmp_path / "corpus", SIZE)


def test_generated_corpus_is_deterministic(tmp_path):
    a = corpus_gen.generate(tmp_path / "a", 10)
    b = corpus_gen.generate(tmp_path / "b", 10)
    for path in sorted(a.glob("*.json")):
        assert path.read_text() == (b / path.name).read_text()


def test_revision_changes_content(tmp_path):
    directory = corpus_gen.generate(tmp_path / "c", 5)
    before = (directory / "WL-EXP-00002.json").read_text()
    corpus_gen.revise_letter(directory, 2, revision=1)
    assert (directory / "WL-EXP-00002.json").read_text() != before


def test_every_condition_builds_the_same_initial_index(corpus):
    signatures = set()
    for factory in ALL:
        condition = factory(corpus)
        condition.initial_build()
        hits = condition.search("audit trail disabled shared account", 5)
        signatures.add(tuple((h.letter_id, h.chunk_id) for h in hits))
    assert len(signatures) == 1, "conditions must start from an identical index"


def test_static_baseline_never_sees_new_content(corpus):
    condition = StaticIndex(corpus)
    condition.initial_build()
    new_id = corpus_gen.add_letter(corpus, SIZE + 1)
    condition.absorb_change()
    assert condition.store.current(new_id) is None


def test_updating_conditions_all_see_new_content(corpus):
    for factory in (FullRebuild, WarnSync):
        directory = corpus_gen.generate(corpus.parent / factory.__name__, SIZE)
        condition = factory(directory)
        condition.initial_build()
        new_id = corpus_gen.add_letter(directory, SIZE + 1)
        condition.absorb_change()
        assert condition.store.current(new_id) is not None, factory.__name__


def test_rebuild_reprocesses_everything_and_warnsync_does_not(corpus):
    result = experiment_ingestion(SIZE, cycles=2)
    by_name = {row["condition"]: row for row in result["rows"]}
    assert by_name["B1"]["corpus_reprocessed_pct"] == 0.0
    assert by_name["B2"]["corpus_reprocessed_pct"] > 90.0
    assert by_name["WS"]["corpus_reprocessed_pct"] < 25.0
    assert by_name["WS"]["chunks_embedded_per_cycle"] < \
        by_name["B2"]["chunks_embedded_per_cycle"]


def test_synchronous_baseline_does_its_work_on_the_query_path(corpus):
    result = experiment_ingestion(SIZE, cycles=2)
    by_name = {row["condition"]: row for row in result["rows"]}
    assert by_name["B3"]["work_on_query_path"] is True
    assert by_name["WS"]["work_on_query_path"] is False


def test_incremental_ingestion_converges_to_a_full_rebuild(corpus):
    """The soundness claim behind every efficiency number in the results."""
    result = experiment_equivalence(SIZE, cycles=4)
    assert result["identical_result_lists"] == result["queries"]
    assert result["mean_jaccard_vs_rebuild"] == 1.0
    assert result["mean_kendall_tau_vs_rebuild"] == 1.0


def test_jaccard_and_tau_edge_cases():
    assert jaccard([], []) == 1.0
    assert jaccard(["a"], []) == 0.0
    assert jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert kendall_tau(["a", "b"], ["a", "b"]) == 1.0
    assert kendall_tau(["a", "b"], ["b", "a"]) == -1.0
    assert kendall_tau(["a"], ["a"]) != kendall_tau(["a"], ["a"])  # NaN: undefined


def test_percentile_bounds():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 100) == 100
    assert percentile([], 50) != percentile([], 50)  # NaN
