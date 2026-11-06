"""Split article text into TTS-sized chunks on sentence boundaries."""

from __future__ import annotations

import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREV_RE = re.compile(r"\b(e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Ms|St|no|No)\.$")


def chunk_text(text: str, max_chars: int = 3000) -> list[str]:  # type: ignore[valid-type]
    """Split text into chunks of at most ~max_chars, never mid-sentence.

    Sentences longer than max_chars are hard-split at word boundaries.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        for part in _hard_wrap(sentence, max_chars):
            if length + len(part) + 1 > max_chars and current:
                chunks.append(" ".join(current))
                current, length = [], 0
            current.append(part)
            length += len(part) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_sentences(text: str) -> list[str]:  # type: ignore[valid-type]
    parts = _SENTENCE_RE.split(text)
    # keep obvious abbreviations attached: rejoin fragments ending in e.g. / etc.
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and _ABBREV_RE.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _hard_wrap(sentence: str, max_chars: int) -> list[str]:  # type: ignore[valid-type]
    if len(sentence) <= max_chars:
        return [sentence]
    words = sentence.split()
    out: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if length + len(word) + 1 > max_chars and current:
            out.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        out.append(" ".join(current))
    return out
