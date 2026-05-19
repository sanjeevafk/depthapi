from scripts.ingest_corpus.research_corpus.config import ValidationConfig
from scripts.ingest_corpus.research_corpus.validation import validate_chunks


def test_validation_flags_broken_fences() -> None:
    rows = [
        {"chunk_id": "a", "content": "```python\nprint('oops')"},
        {"chunk_id": "b", "content": "clean chunk with enough content " * 5},
    ]
    report = validate_chunks(rows, ValidationConfig(min_chars=10, max_chars=500))
    assert report["issue_counts"]["broken_markdown"] == 1
    assert report["issue_counts"]["code_fence_corruption"] == 1
