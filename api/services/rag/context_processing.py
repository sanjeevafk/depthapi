"""Retrieval context ordering."""
from __future__ import annotations

from typing import Any


def reorder_lost_in_the_middle(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder contexts into a U-shaped distribution to prevent lost-in-the-middle degradation.

    Places highest-scoring contexts at the beginning and end of the prompt context,
    where LLM attention weight is strongest:
    Input ranks:  [0, 1, 2, 3, 4]
    Output ranks: [0, 2, 4, 3, 1]
    """
    if len(contexts) <= 2:
        return list(contexts)

    reordered: list[dict[str, Any]] = [None] * len(contexts)  # type: ignore[list-item]
    left = 0
    right = len(contexts) - 1

    for i, ctx in enumerate(contexts):
        if i % 2 == 0:
            reordered[left] = ctx
            left += 1
        else:
            reordered[right] = ctx
            right -= 1

    return reordered
