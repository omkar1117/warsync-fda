"""Corpus sources and metadata extraction — the seam to the private pipeline.

A `CorpusSource` answers one question: what does the upstream corpus look like
right now? The sync engine does not care whether that answer came from a live
poll of a public listing or from a directory of JSON files.

Included here: `JsonDirectorySource`, a file-backed source that makes the whole
system runnable and testable offline, and `RegexMetadataExtractor`, which pulls
CFR/statute citations out of enforcement prose.

Not included here: the production source that polls the live FDA warning-letter
listing, fetches letter HTML, and OCRs scanned PDFs; and the learned metadata
extractor. `LiveListingSource` below marks that seam explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from .models import SourceRecord

#: Runs of whitespace, page furniture and navigation chrome vary between fetches
#: of an unchanged letter; canonicalizing before hashing keeps the fingerprint
#: keyed to the enforcement text rather than to the page it arrived on.
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_BOILERPLATE_RE = re.compile(
    r"^(?:skip to main content|u\.s\. food and drug administration|"
    r"an official website of the united states government|"
    r"share|tweet|linkedin|email|print)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

#: "21 CFR 211.192", "21 C.F.R. 211.22(a)", "21 CFR Part 820.75"
_CFR_RE = re.compile(
    r"\b(\d{1,2})\s*C\.?\s*F\.?\s*R\.?\s*(?:Part\s+)?(\d{1,4}(?:\.\d+)?(?:\([a-zA-Z0-9]{1,3}\))*)",
    re.IGNORECASE,
)
#: "section 501(a)(2)(B)", "§ 502(f)(1)" of the FD&C Act
_STATUTE_RE = re.compile(
    r"(?:§|[Ss]ection)\s*(\d{3}(?:\([a-zA-Z0-9]{1,3}\))*)\s*(?:of the (?:Federal )?"
    r"Food,?\s*Drug,?\s*and Cosmetic Act|of the (?:FD&C|FDC) Act|\[21 U\.S\.C)",
)


class CorpusSource(Protocol):
    """Yields the upstream corpus as it currently stands."""

    def fetch(self) -> Iterable[SourceRecord]: ...


class MetadataExtractor(Protocol):
    """Fills in structured fields the source did not already supply."""

    def __call__(self, record: SourceRecord) -> SourceRecord: ...


def canonicalize(content: str) -> str:
    """Normalize fetched content so an unchanged letter hashes identically."""
    text = content.replace("\r\n", "\n")
    text = _BOILERPLATE_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def fingerprint(content: str) -> str:
    """SHA-256 of the canonical content — the change-detection primitive."""
    return hashlib.sha256(canonicalize(content).encode("utf-8")).hexdigest()


class RegexMetadataExtractor:
    """Pattern-rule extraction of cited provisions, plus a subject-line guess.

    The production extractor adds named-entity extraction for recipient and
    issuing office; this one only fills fields the source left blank, so a
    source that already knows its metadata passes through untouched.
    """

    def __call__(self, record: SourceRecord) -> SourceRecord:
        citations = record.cfr_citations or tuple(extract_citations(record.content))
        subject = record.subject or _first_line(record.content)
        return SourceRecord(
            letter_id=record.letter_id,
            content=record.content,
            posting_date=record.posting_date,
            issuance_date=record.issuance_date,
            recipient=record.recipient,
            office=record.office,
            subject=subject,
            cfr_citations=citations,
            source_url=record.source_url,
            synthetic=record.synthetic,
        )


def extract_citations(text: str) -> list[str]:
    """Normalized, de-duplicated citation strings in first-appearance order."""
    found: list[str] = []
    for title, section in _CFR_RE.findall(text):
        citation = f"{title} CFR {section}"
        if citation not in found:
            found.append(citation)
    for section in _STATUTE_RE.findall(text):
        citation = f"FD&C Act § {section}"
        if citation not in found:
            found.append(citation)
    return found


def _first_line(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:200]
    return ""


class JsonDirectorySource:
    """Reads `*.json` letter records from a directory.

    Each file is one letter: `{"letter_id": ..., "content": ..., ...}`. Deleting
    a file is how the offline demo exercises the REMOVED path, and editing one
    is how it exercises MODIFIED — the same two cases the live poller sees when
    the agency revises or withdraws a letter.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def fetch(self) -> Iterator[SourceRecord]:
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.json")):
            if path.name.startswith("_"):
                continue
            yield self._load(path)

    @staticmethod
    def _load(path: Path) -> SourceRecord:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SourceRecord(
            letter_id=data.get("letter_id") or path.stem,
            content=data["content"],
            posting_date=data.get("posting_date", ""),
            issuance_date=data.get("issuance_date", ""),
            recipient=data.get("recipient", ""),
            office=data.get("office", ""),
            subject=data.get("subject", ""),
            cfr_citations=tuple(data.get("cfr_citations", ())),
            source_url=data.get("source_url", ""),
            synthetic=bool(data.get("synthetic", False)),
        )


class LiveListingSource:
    """Placeholder for the production poller — deliberately not implemented here.

    The live source (scheduled polling of the public FDA warning-letter listing,
    letter HTML retrieval, OCR for scanned PDFs, and retry/backoff policy) is
    part of the WarnSync research prototype and is not distributed with this
    reference implementation. Implement `fetch()` against the same
    `CorpusSource` protocol and the rest of this package works unchanged.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(
            "LiveListingSource is not part of the open reference implementation. "
            "Implement the CorpusSource protocol (fetch() -> Iterable[SourceRecord]) "
            "against your own listing poller and pass it to SyncEngine."
        )
