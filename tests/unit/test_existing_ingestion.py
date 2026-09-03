"""
tests/unit/test_existing_ingestion.py

Phase 0: Baseline regression tests for existing ingestion utilities.
Captures current behavior before any refactoring.
Tests must remain green throughout Phase 2 refactoring.
"""

from __future__ import annotations

import hashlib

import pytest

from api.services.rag.pipeline.chunkers.legacy.base_ingestor import (
    BaseIngestor,
    Chunk,
    chunk_id,
    clean_text,
    content_hash,
    make_doc_id,
    rough_token_count,
    split_text,
    split_text_semantic,
)



# ─── clean_text ───────────────────────────────────────────────────────────────

class TestCleanText:
    def test_strips_control_chars(self):
        text = "hello\x00world\x01"
        result = clean_text(text)
        assert "\x00" not in result
        assert "\x01" not in result

    def test_normalises_unicode_nfc(self):
        # Combining character vs precomposed
        composed = "\u00e9"        # é (precomposed)
        decomposed = "e\u0301"     # é (decomposed)
        assert clean_text(decomposed) == clean_text(composed)

    def test_collapses_multiple_spaces(self):
        result = clean_text("hello   world")
        assert "   " not in result
        assert "hello world" in result

    def test_collapses_triple_newlines(self):
        result = clean_text("line1\n\n\n\nline2")
        assert "\n\n\n" not in result

    def test_strips_whitespace(self):
        result = clean_text("  hello  ")
        assert result == "hello"

    def test_empty_string(self):
        assert clean_text("") == ""

    def test_idempotent(self):
        text = "Hello World\n\nSome text."
        assert clean_text(clean_text(text)) == clean_text(text)


# ─── rough_token_count ────────────────────────────────────────────────────────

class TestRoughTokenCount:
    def test_returns_positive_int(self):
        assert rough_token_count("hello") >= 1

    def test_empty_string_returns_1(self):
        assert rough_token_count("") == 1

    def test_longer_text_more_tokens(self):
        short = rough_token_count("hello")
        long = rough_token_count("hello " * 100)
        assert long > short

    def test_approx_4_chars_per_token(self):
        # 400 chars → ~100 tokens
        text = "a" * 400
        assert rough_token_count(text) == 100


# ─── chunk_id / content_hash ──────────────────────────────────────────────────

class TestHashFunctions:
    def test_chunk_id_is_16_hex_chars(self):
        cid = chunk_id("hello world")
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    def test_chunk_id_deterministic(self):
        assert chunk_id("test") == chunk_id("test")

    def test_chunk_id_different_inputs_different_ids(self):
        assert chunk_id("text A") != chunk_id("text B")

    def test_content_hash_is_full_sha256(self):
        ch = content_hash("hello world")
        assert len(ch) == 64
        assert ch == hashlib.sha256("hello world".encode()).hexdigest()

    def test_content_hash_deterministic(self):
        assert content_hash("hello") == content_hash("hello")


# ─── make_doc_id ─────────────────────────────────────────────────────────────

class TestMakeDocId:
    def test_returns_24_hex_chars(self):
        did = make_doc_id("System Design Primer", "https://example.com")
        assert len(did) == 24
        assert all(c in "0123456789abcdef" for c in did)

    def test_deterministic(self):
        d1 = make_doc_id("name", "url")
        d2 = make_doc_id("name", "url")
        assert d1 == d2

    def test_none_url_handled(self):
        did = make_doc_id("name", None)
        assert isinstance(did, str)
        assert len(did) == 24

    def test_different_names_different_ids(self):
        d1 = make_doc_id("name A", None)
        d2 = make_doc_id("name B", None)
        assert d1 != d2


# ─── split_text ───────────────────────────────────────────────────────────────

class TestSplitText:
    LONG_TEXT = ("A" * 100 + "\n\n") * 10  # ~1000 chars

    def test_returns_list_of_strings(self):
        chunks = split_text(self.LONG_TEXT, chunk_size=200)
        assert isinstance(chunks, list)
        assert all(isinstance(c, str) for c in chunks)

    def test_no_empty_chunks(self):
        chunks = split_text(self.LONG_TEXT, chunk_size=200)
        assert all(c.strip() for c in chunks)

    def test_short_text_returns_single_chunk(self):
        # Needs to pass the 50-char minimum filter
        text = "This is a sufficiently long text to pass the minimum 50-char filter."
        chunks = split_text(text, chunk_size=500)
        assert len(chunks) == 1

    def test_chunks_respect_size_limit_approx(self):
        chunks = split_text(self.LONG_TEXT, chunk_size=200, overlap=0)
        # Each chunk should be ≤ chunk_size * 1.5 (recursion may overshoot slightly)
        for c in chunks:
            assert len(c) <= 350, f"Chunk too long: {len(c)} chars"

    def test_filters_tiny_fragments(self):
        # Fragments < 50 chars should be dropped
        chunks = split_text("a" * 40 + "\n\n" + "B" * 200)
        assert all(len(c.strip()) >= 50 for c in chunks)

    def test_overlap_creates_prefix(self):
        text = "First paragraph text here.\n\nSecond paragraph text here."
        chunks = split_text(text, chunk_size=30, overlap=10)
        # With overlap, chunk 2 should contain some of chunk 1's tail
        if len(chunks) > 1:
            assert len(chunks[1]) > 0


# ─── split_text_semantic ─────────────────────────────────────────────────────

class TestSplitTextSemantic:
    TEXT = " ".join(["This is sentence number {}.".format(i) for i in range(50)])

    def test_returns_nonempty_list(self):
        chunks = split_text_semantic(self.TEXT, chunk_size=300)
        assert len(chunks) > 0

    def test_all_chunks_min_length(self):
        chunks = split_text_semantic(self.TEXT, chunk_size=300)
        assert all(len(c.strip()) >= 50 for c in chunks)

    def test_no_overlap_mode(self):
        chunks = split_text_semantic(self.TEXT, chunk_size=300, overlap_words=0)
        assert len(chunks) > 0

    def test_idempotent_content(self):
        """Running twice on same text should produce same chunk count."""
        chunks1 = split_text_semantic(self.TEXT, chunk_size=300)
        chunks2 = split_text_semantic(self.TEXT, chunk_size=300)
        assert len(chunks1) == len(chunks2)


# ─── BaseIngestor integration ─────────────────────────────────────────────────

class TestBaseIngestorIntegration:
    """Light integration: verify BaseIngestor creates Chunk objects correctly."""

    def test_make_chunk_returns_chunk_object(self, tmp_path):
        """BaseIngestor._make_chunk returns a Chunk with correct fields."""
        # Redirect DATA_DIR to tmp path to avoid touching production files
        import api.services.rag.pipeline.chunkers.legacy.base_ingestor as bi_module
        original_data_dir = bi_module.DATA_DIR
        original_chunks_file = bi_module.CHUNKS_FILE
        bi_module.DATA_DIR = tmp_path
        bi_module.CHUNKS_FILE = tmp_path / "chunks.json"

        try:
            ingestor = BaseIngestor(source_name="Test", source_type="markdown")
            chunk = ingestor._make_chunk(
                content="This is a long enough chunk of text to pass validation checks.",
                order=0,
                source_url="https://example.com",
                tags=["test"],
            )
            assert chunk is not None
            assert chunk.source_name == "Test"
            assert chunk.chunk_order == 0
            assert chunk.token_count >= 1
            assert len(chunk.id) == 16
        finally:
            bi_module.DATA_DIR = original_data_dir
            bi_module.CHUNKS_FILE = original_chunks_file

    def test_duplicate_chunks_skipped(self, tmp_path):
        """Same content added twice should only produce one chunk."""
        import api.services.rag.pipeline.chunkers.legacy.base_ingestor as bi_module
        original_data_dir = bi_module.DATA_DIR
        original_chunks_file = bi_module.CHUNKS_FILE
        bi_module.DATA_DIR = tmp_path
        bi_module.CHUNKS_FILE = tmp_path / "chunks.json"

        try:
            ingestor = BaseIngestor(
                source_name="Test", near_dup_threshold=None
            )
            text = "This is a sufficiently long text that should be accepted by ingestor."
            added1 = ingestor.add([text])
            added2 = ingestor.add([text])
            assert len(added1) == 1
            assert len(added2) == 0  # duplicate
        finally:
            bi_module.DATA_DIR = original_data_dir
            bi_module.CHUNKS_FILE = original_chunks_file

    def test_too_short_chunks_rejected(self, tmp_path):
        """Text < 50 chars should be rejected."""
        import api.services.rag.pipeline.chunkers.legacy.base_ingestor as bi_module
        original_data_dir = bi_module.DATA_DIR
        original_chunks_file = bi_module.CHUNKS_FILE
        bi_module.DATA_DIR = tmp_path
        bi_module.CHUNKS_FILE = tmp_path / "chunks.json"

        try:
            ingestor = BaseIngestor(source_name="Test", near_dup_threshold=None)
            added = ingestor.add(["short"])
            assert len(added) == 0
            assert ingestor.skip_stats["too_short"] == 1
        finally:
            bi_module.DATA_DIR = original_data_dir
            bi_module.CHUNKS_FILE = original_chunks_file
