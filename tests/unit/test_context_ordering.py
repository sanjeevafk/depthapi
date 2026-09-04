"""Unit tests for Lost-in-the-Middle context ordering."""
from __future__ import annotations

from api.services.rag.context_processing import reorder_lost_in_the_middle


def test_reorder_empty_and_small():
    assert reorder_lost_in_the_middle([]) == []
    single = [{"content": "doc1"}]
    assert reorder_lost_in_the_middle(single) == single
    double = [{"content": "doc1"}, {"content": "doc2"}]
    assert reorder_lost_in_the_middle(double) == double


def test_reorder_odd_contexts():
    # Input ranks: 0 (best), 1, 2, 3, 4 (worst)
    items = [{"id": f"doc_{i}", "rank": i} for i in range(5)]
    reordered = reorder_lost_in_the_middle(items)

    reordered_ranks = [item["rank"] for item in reordered]
    # Expected U-shape: [0, 2, 4, 3, 1]
    # Rank 0 at index 0 (top of prompt)
    # Rank 1 at index 4 (bottom of prompt, closest to generation head)
    # Worst rank 4 in the middle
    assert reordered_ranks == [0, 2, 4, 3, 1]


def test_reorder_even_contexts():
    # Input ranks: 0, 1, 2, 3
    items = [{"id": f"doc_{i}", "rank": i} for i in range(4)]
    reordered = reorder_lost_in_the_middle(items)

    reordered_ranks = [item["rank"] for item in reordered]
    # Expected U-shape: [0, 2, 3, 1]
    assert reordered_ranks == [0, 2, 3, 1]
