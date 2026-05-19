from scripts.ingest_corpus.research_corpus.chunker import DeterministicChunker
from scripts.ingest_corpus.research_corpus.config import ChunkingConfig


def test_chunker_preserves_code_fences() -> None:
    document = {
        "document_id": "doc-1",
        "source": "unit-test",
        "source_url": "https://example.com",
        "upstream_license": "MIT",
        "retrieved_at": "2026-05-19T00:00:00Z",
        "title": "Sample",
        "namespace": "tests",
        "metadata": {},
        "content": "# Intro\n\nParagraph.\n\n```python\nprint('hello')\nprint('world')\n```\n\nMore explanation.",
    }
    chunker = DeterministicChunker(ChunkingConfig(chunk_size=80, overlap=10))
    chunks = chunker.chunk_document(document)

    assert chunks
    assert any("```python" in chunk["content"] for chunk in chunks)
    assert all(chunk["content"].count("```") % 2 == 0 for chunk in chunks)


def test_chunker_is_deterministic() -> None:
    document = {
        "document_id": "doc-1",
        "source": "unit-test",
        "source_url": "https://example.com",
        "upstream_license": "MIT",
        "retrieved_at": "2026-05-19T00:00:00Z",
        "title": "Sample",
        "namespace": "tests",
        "metadata": {},
        "content": "# Intro\n\nOne sentence. Two sentence. Three sentence.",
    }
    chunker = DeterministicChunker(ChunkingConfig(chunk_size=40, overlap=5))
    first = chunker.chunk_document(document)
    second = chunker.chunk_document(document)
    assert first == second
