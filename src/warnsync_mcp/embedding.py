"""Text tokenization, chunking and a dependency-free placeholder embedder.

The production system embeds chunks with a neural model; that model is not part
of this repository. What *is* here is the `Embedder` protocol the rest of the
serving layer talks to, plus `HashingEmbedder` — a deterministic hashed
bag-of-words vectorizer that needs no model download, so the open reference
implementation runs and its tests pass anywhere.

Swap it out by passing any callable with the same shape to `SyncEngine` and
`VersionedStore`; nothing else in the package needs to change.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable, Protocol, Sequence

#: Keeps regulatory tokens intact: "211.192", "21 CFR 211.22(a)", "483".
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/()-]*")

DEFAULT_DIM = 256


class Embedder(Protocol):
    """Anything that turns text into a fixed-length vector."""

    dim: int

    def __call__(self, text: str) -> tuple[float, ...]: ...


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Hashing-trick bag of words with sublinear term frequency, L2-normalized.

    Deterministic across processes and machines (blake2b, not Python's salted
    `hash`), which matters because fingerprints and embeddings are compared
    across runs.
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def __call__(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return tuple(vec)
        return tuple(v / norm for v in vec)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two already-comparable vectors (0.0 if either is null)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def chunk_text(
    text: str,
    target_tokens: int = 512,
    overlap_tokens: int = 48,
) -> list[str]:
    """Split at paragraph granularity, packing paragraphs up to ~`target_tokens`.

    Paragraph boundaries are preserved because enforcement letters make one
    discrete allegation per paragraph; a chunk that straddles two violations
    retrieves badly for both. Oversized paragraphs are hard-split with overlap.

    `target_tokens` counts the whitespace-ish tokens `tokenize` produces, not
    subwords: a 512-token budget here is roughly 650-700 subword tokens, so
    lower it if you are packing chunks into a tight embedding context.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            chunks.append("\n\n".join(buffer))
            buffer, buffer_len = [], 0

    for paragraph in paragraphs:
        length = len(tokenize(paragraph))
        if length > target_tokens:
            flush()
            chunks.extend(_split_long(paragraph, target_tokens, overlap_tokens))
            continue
        if buffer_len + length > target_tokens:
            flush()
        buffer.append(paragraph)
        buffer_len += length
    flush()
    return chunks or ([text.strip()] if text.strip() else [])


def _split_long(paragraph: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    words = paragraph.split()
    step = max(1, target_tokens - overlap_tokens)
    return [
        " ".join(words[start : start + target_tokens])
        for start in range(0, len(words), step)
        if words[start : start + target_tokens]
    ]
