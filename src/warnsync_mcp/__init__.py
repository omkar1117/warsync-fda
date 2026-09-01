"""WarnSync MCP — the open reference implementation of the WarnSync serving layer.

This package contains the *protocol and versioning* half of the WarnSync design:
a versioned hybrid store with atomic per-letter visibility swaps, an idempotent
fingerprint-diff sync engine, and an MCP server that exposes a stable tool
surface plus protocol-native corpus-freshness notifications.

The production corpus acquisition pipeline (live FDA polling, PDF/OCR text
extraction, the neural embedding model, and the evaluation harness) is *not*
part of this package. Those live behind the `CorpusSource`, `MetadataExtractor`
and `Embedder` seams in `sources.py` / `embedding.py`, so the serving layer can
be published, reviewed and reproduced on its own.
"""

from .models import ChangeEvent, ChangeKind, Chunk, LetterVersion, SourceRecord
from .store import VersionedStore
from .ingest import SyncEngine

__version__ = "0.1.0"

__all__ = [
    "ChangeEvent",
    "ChangeKind",
    "Chunk",
    "LetterVersion",
    "SourceRecord",
    "VersionedStore",
    "SyncEngine",
    "__version__",
]
