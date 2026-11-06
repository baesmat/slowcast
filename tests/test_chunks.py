from __future__ import annotations

from slowcast.chunks import chunk_text

LONG = " ".join(
    f"Sentence number {i} says something moderately long for testing." for i in range(300)
)


def test_short_text_single_chunk():
    assert chunk_text("Hello world.") == ["Hello world."]


def test_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_long_text_chunk_sizes():
    chunks = chunk_text(LONG, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) > 1
    # no text lost: rejoining chunks reproduces the input words
    assert " ".join(chunks).split() == LONG.split()


def test_order_preserved():
    chunks = chunk_text(LONG, max_chars=500)
    joined = " ".join(chunks)
    assert joined.index("Sentence number 0 ") < joined.index("Sentence number 50 ")
    assert joined.index("Sentence number 299 ") > joined.index("Sentence number 100 ")


def test_giant_sentence_hard_wrap():
    sentence = "word " * 2000
    chunks = chunk_text(sentence.strip(), max_chars=300)
    assert all(len(c) <= 300 for c in chunks)
    assert len(chunks) >= 6


def test_abbreviations_stay_attached():
    text = "This costs five dollars e.g. for shipping. And it arrives Monday. Mr. Smith confirmed."
    chunks = chunk_text(text, max_chars=30)
    joined = " ".join(chunks)
    # the abbreviation should not be stranded alone at a chunk boundary
    assert "e.g. for" in joined or "e.g." in chunks[0]
