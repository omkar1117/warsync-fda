# Sample corpus

Every letter in `sample_corpus/` and `incoming/` is **synthetic**. The firms,
people, dates, lot numbers and findings are invented. Nothing here reproduces a
real FDA warning letter or describes a real enforcement action, and every record
carries `"synthetic": true` plus an in-text banner so it cannot be mistaken for
one downstream.

They exist so the reference implementation is runnable and testable offline:
the prose is written in the register of real enforcement correspondence and
cites real CFR provisions, which is what the citation extractor, the chunker and
the retriever need in order to be exercised meaningfully.

To serve a real corpus, point `--corpus` at your own directory of records in the
same shape, or implement the `CorpusSource` protocol (see
`src/warnsync_mcp/sources.py`).

## Record shape

```json
{
  "letter_id": "WL-2025-0412",
  "content": "full letter text",
  "recipient": "Northgate Sterile Solutions, Inc.",
  "office": "Center for Drug Evaluation and Research (CDER)",
  "issuance_date": "2025-04-12",
  "posting_date": "2025-04-22",
  "subject": "Warning Letter — CGMP violations",
  "source_url": "https://example.invalid/...",
  "cfr_citations": ["optional; extracted from content when omitted"],
  "synthetic": true
}
```

## `incoming/`

`incoming/WL-2026-0224.json` is held back from the served corpus so the demos
can drop it in mid-session and show change detection, the atomic commit and the
resulting freshness notification happen live. `scripts/demo_live.py` does this
automatically and cleans up after itself.
