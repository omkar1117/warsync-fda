"""Canonicalization, fingerprinting, citation extraction and chunking."""

from warnsync_mcp.embedding import HashingEmbedder, chunk_text, cosine, tokenize
from warnsync_mcp.models import SourceRecord
from warnsync_mcp.sources import (
    RegexMetadataExtractor,
    canonicalize,
    extract_citations,
    fingerprint,
)


def test_fingerprint_ignores_whitespace_and_boilerplate():
    plain = "Dear Sir:\n\nYour firm failed to investigate."
    noisy = "Skip to main content\nDear   Sir:\n\n\n\nYour firm  failed to investigate.  \n"
    assert fingerprint(plain) == fingerprint(noisy)


def test_fingerprint_changes_when_content_changes():
    assert fingerprint("finding one") != fingerprint("finding one and two")


def test_canonicalize_is_idempotent():
    text = "  A\t B \n\n\n C  "
    assert canonicalize(canonicalize(text)) == canonicalize(text)


def test_extract_citations_normalizes_forms():
    text = (
        "violates 21 CFR 211.192, 21 C.F.R. 820.75(a) and 21 CFR Part 111, "
        "and section 501(a)(2)(B) of the Federal Food, Drug, and Cosmetic Act"
    )
    citations = extract_citations(text)
    assert "21 CFR 211.192" in citations
    assert "21 CFR 820.75(a)" in citations
    assert "21 CFR 111" in citations
    assert "FD&C Act § 501(a)(2)(B)" in citations


def test_extract_citations_deduplicates_preserving_order():
    citations = extract_citations("21 CFR 211.22(a) ... later 21 CFR 211.192 ... 21 CFR 211.22(a)")
    assert citations == ["21 CFR 211.22(a)", "21 CFR 211.192"]


def test_extractor_does_not_override_source_supplied_metadata():
    record = SourceRecord(
        letter_id="X",
        content="cites 21 CFR 211.192",
        cfr_citations=("21 CFR 999.1",),
        subject="supplied subject",
    )
    enriched = RegexMetadataExtractor()(record)
    assert enriched.cfr_citations == ("21 CFR 999.1",)
    assert enriched.subject == "supplied subject"


def test_chunking_respects_paragraph_boundaries():
    paragraphs = [f"Paragraph {i}. " + "word " * 200 for i in range(4)]
    chunks = chunk_text("\n\n".join(paragraphs), target_tokens=250)
    assert len(chunks) == 4
    assert all(chunk.startswith("Paragraph ") for chunk in chunks)


def test_chunking_packs_short_paragraphs_together():
    text = "\n\n".join(["short one", "short two", "short three"])
    assert chunk_text(text, target_tokens=512) == [text]


def test_chunking_splits_an_oversized_paragraph_with_overlap():
    chunks = chunk_text("word " * 300, target_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1
    assert all(len(tokenize(chunk)) <= 100 for chunk in chunks)


def test_chunking_handles_empty_input():
    assert chunk_text("   ") == []


def test_embedder_is_deterministic_and_normalized():
    embed = HashingEmbedder()
    a, b = embed("sterility test failure"), embed("sterility test failure")
    assert a == b
    assert abs(cosine(a, b) - 1.0) < 1e-9


def test_embedder_separates_unrelated_text():
    embed = HashingEmbedder()
    related = cosine(embed("audit trail disabled"), embed("audit trail review"))
    unrelated = cosine(embed("audit trail disabled"), embed("dietary supplement labeling"))
    assert related > unrelated
