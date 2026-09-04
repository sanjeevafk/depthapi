from __future__ import annotations

import pandas as pd

from evaluation.analyze_results import (
    _expected_ids,
    _mrr,
    _precision_at_k,
    _recall_at_k,
    _retrieved_ids,
)


def test_retrieval_metric_ids_are_canonicalized_across_aliases():
    row = pd.Series(
        {
            "expected_doc_ids": [" Doc:My/Document "],
            "relevant_chunk_ids": ["Chunk ABC_123"],
            "contexts": [
                {
                    "document_id": "my-document",
                    "chunk_id": "abc_123",
                    "metadata": {"doc_id": "ignored-duplicate"},
                }
            ],
        }
    )

    expected_docs = _expected_ids(row, "relevant_doc_ids")
    retrieved_docs = _retrieved_ids(row, "doc_id")
    expected_chunks = _expected_ids(row, "relevant_chunk_ids")
    retrieved_chunks = _retrieved_ids(row, "chunk_id")

    assert _recall_at_k(expected_docs, retrieved_docs, 5) == 1.0
    assert _precision_at_k(expected_chunks, retrieved_chunks, 5) == 1.0
    assert _mrr(expected_chunks, retrieved_chunks) == 1.0
