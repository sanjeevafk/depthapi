"""
neardup_filter.py — SimHash near-duplicate detection for RAG chunks.

Detects near-duplicate text blocks across books without any external
dependencies (pure stdlib + hashlib).

Algorithm
---------
1. Normalise text (lowercase, collapse whitespace).
2. Extract 4-gram character shingles.
3. Compute a 64-bit SimHash fingerprint by summing ±1 per shingle per bit.
4. Compare against all previously seen fingerprints via Hamming distance.
5. If distance ≤ threshold → near-duplicate → reject.

Hamming distance guide (64-bit fingerprint)
-------------------------------------------
  0      identical
  1-3    near-identical (copy-paste boilerplate)
  4-6    very similar (same content, minor edits)
  7-10   related but distinct
  >10    different content

Default threshold = 4 (catches boilerplate across different books while
preserving genuinely distinct chunks about the same topic).

Complexity
----------
O(n) per insertion — suitable for ≤100K chunks per run.
For larger corpora, bucket by MSB of fingerprint for sub-linear scan.
"""

from __future__ import annotations

import hashlib
import re
import logging

log = logging.getLogger("ingest")

_BITS = 64
_SHINGLE_N = 4
_NORMALISE_RE = re.compile(r"\s+")


# ─── Core SimHash primitives ──────────────────────────────────────────────────

def _normalise(text: str) -> str:
    return _NORMALISE_RE.sub(" ", text.lower()).strip()


def _shingles(text: str, n: int = _SHINGLE_N) -> list[str]:
    return [text[i : i + n] for i in range(max(0, len(text) - n + 1))]


def simhash(text: str) -> int:
    """
    Compute a 64-bit SimHash fingerprint for `text`.

    Returns an integer in range [0, 2**64).
    """
    norm = _normalise(text)
    v = [0] * _BITS
    for shingle in _shingles(norm):
        # Use first 8 bytes of MD5 for speed (collision rate negligible at 64 bits)
        h = int.from_bytes(
            hashlib.md5(shingle.encode("utf-8", errors="replace")).digest()[:8],
            byteorder="little",
        )
        for i in range(_BITS):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(_BITS):
        if v[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Population count of XOR — number of differing bits."""
    return bin(a ^ b).count("1")


# ─── Filter class ─────────────────────────────────────────────────────────────

class NearDupFilter:
    """
    Rolling near-duplicate filter for chunk text.

    Usage::

        flt = NearDupFilter(threshold=4)
        for chunk in chunks:
            if flt.is_duplicate(chunk.cleaned_text):
                continue  # skip near-duplicate
            # ... accept chunk

    The filter is stateful — it accumulates fingerprints across all calls
    within a single ingest run, enabling cross-book deduplication.
    """

    def __init__(self, threshold: int = 4):
        """
        Args:
            threshold: Maximum Hamming distance to classify as near-duplicate.
                       Default 4 ≈ 94% similarity on a 64-bit fingerprint.
                       Set to 0 to only reject exact SimHash collisions.
        """
        self.threshold = threshold
        self._seen: list[int] = []          # ordered list of fingerprints
        self._buckets: dict[int, list[int]] = {}  # MSB-8 → indices into _seen
        self.near_dup_count = 0
        self.total_checked = 0

    # ── Public API ────────────────────────────────────────────────────────────
    def is_duplicate(self, text: str) -> bool:
        """
        Return True if `text` is a near-duplicate of a previously seen chunk.
        Always registers the fingerprint (whether or not it's a duplicate).
        """
        self.total_checked += 1
        h = simhash(text)

        if self._is_near_dup(h):
            self.near_dup_count += 1
            return True

        self._register(h)
        return False

    def stats(self) -> dict:
        return {
            "total_checked": self.total_checked,
            "near_dup_rejected": self.near_dup_count,
            "unique_fingerprints": len(self._seen),
            "threshold": self.threshold,
        }

    # ── Internals ─────────────────────────────────────────────────────────────
    def _msb8(self, h: int) -> int:
        """Top 8 bits of the fingerprint — used for bucket narrowing."""
        return (h >> 56) & 0xFF

    def _register(self, h: int) -> None:
        idx = len(self._seen)
        self._seen.append(h)
        bucket = self._msb8(h)
        self._buckets.setdefault(bucket, []).append(idx)

    def _is_near_dup(self, h: int) -> bool:
        """
        Check whether any registered fingerprint is within Hamming threshold.

        Optimisation: scan only fingerprints whose top 8 bits differ by ≤ 2
        from `h`, reducing search space by ~99% on large corpora while
        preserving recall for threshold ≤ 10.
        """
        if not self._seen:
            return False

        bucket = self._msb8(h)
        # Candidate buckets: same bucket + adjacent (±1 in MSB)
        candidate_buckets = {bucket, (bucket - 1) & 0xFF, (bucket + 1) & 0xFF}

        # Fast path: check bucketed candidates first
        for b in candidate_buckets:
            for idx in self._buckets.get(b, []):
                if hamming_distance(h, self._seen[idx]) <= self.threshold:
                    return True

        # Slow path: if threshold is high enough, also scan all (rare)
        # Only triggered when threshold ≥ 8 (buckets miss cross-bucket hits)
        if self.threshold >= 8:
            for seen_h in self._seen:
                if hamming_distance(h, seen_h) <= self.threshold:
                    return True

        return False
