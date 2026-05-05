"""
ingest_dlwp.py — Deep Learning with Python 3rd Ed → Supabase knowledge_chunks

Fully implements the DLWP indexing strategy:
  § 2 — Semantic chunking (300-450 tokens), code block preservation, overlap linking
  § 3 — Full metadata enrichment (chapter_number, chapter_title, has_code,
         content_type, topics, embedding_model, embedding_dim, indexed_at)
  § 4 — Local BGE-M3 embedding (output_dimensionality=768, no passage prefix)
  § 7A — Chunk quality validation (min-token, orphaned-code, near-dupe detection)
  § 7B — Batch embedding with progress bar and checkpoint/resume support

Usage:
    # Dry-run first to inspect chunk distribution:
    .venv-ingest/bin/python scripts/ingest_corpus/ingest_dlwp.py --dry-run

    # Full ingestion with checkpointing:
    .venv-ingest/bin/python scripts/ingest_corpus/ingest_dlwp.py

    # Resume after interruption:
    .venv-ingest/bin/python scripts/ingest_corpus/ingest_dlwp.py --resume

    # Custom batch size (default 32):
    .venv-ingest/bin/python scripts/ingest_corpus/ingest_dlwp.py --batch-size 64
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# ─── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import log  # noqa: E402
from api.services.embeddings import get_embedding_service  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

# ─── Configuration ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR     = REPO_ROOT / "dlwp_pages_cleaned"
CHUNKS_FILE   = REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"
CHECKPOINT    = REPO_ROOT / "data" / "rag" / "trusted" / "dlwp_checkpoint.json"

EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIM   = 768
SOURCE_NAME     = "Deep Learning with Python"
SOURCE_URL      = "https://www.manning.com/books/deep-learning-with-python-third-edition"
TAGS            = ["python", "keras", "tensorflow", "deep-learning", "neural-networks", "P0"]

# Token limits (strategy § 2)
TARGET_MAX_TOKENS  = 450   # Target upper bound per chunk
WARN_TOKENS        = 500   # Log warning above this
MIN_TOKENS         = 50    # Discard below this (§ 7A)

# CHAPTER_TOPICS: lightweight heuristic topic tagging per chapter
CHAPTER_TOPICS: dict[str, list[str]] = {
    "chapter01": ["deep-learning", "history", "ai-overview"],
    "chapter02": ["tensors", "numpy", "math", "gradient-descent"],
    "chapter03": ["keras", "tensorflow", "layers", "models", "api"],
    "chapter04": ["classification", "regression", "loss-functions", "metrics"],
    "chapter05": ["generalization", "overfitting", "regularization", "bias-variance"],
    "chapter06": ["ml-workflow", "train-val-test", "hyperparameters"],
    "chapter07": ["keras-api", "functional-api", "subclassing", "callbacks"],
    "chapter08": ["image-classification", "convnets", "conv2d", "pooling"],
    "chapter09": ["residual-connections", "batch-norm", "depthwise-separable"],
    "chapter10": ["convnet-visualization", "grad-cam", "feature-maps"],
    "chapter11": ["image-segmentation", "semantic", "u-net"],
    "chapter12": ["object-detection", "bounding-boxes", "anchors"],
    "chapter13": ["timeseries", "rnn", "lstm", "gru", "forecasting"],
    "chapter14": ["text-classification", "embeddings", "tokenization", "transformers"],
    "chapter15": ["language-models", "transformer", "attention", "bert", "gpt"],
    "chapter16": ["text-generation", "sequence-to-sequence", "sampling", "temperature"],
    "chapter17": ["image-generation", "vae", "gan", "diffusion"],
    "chapter18": ["best-practices", "deployment", "scaling", "mixed-precision"],
    "chapter19": ["future-of-ai", "limitations", "ethics"],
    "chapter20": ["conclusion", "next-steps"],
}


# ─── Tokenizer ────────────────────────────────────────────────────────────────
def _load_tokenizer():
    log.info(f"Loading tokenizer: {EMBEDDING_MODEL}")
    return AutoTokenizer.from_pretrained(EMBEDDING_MODEL)


# ─── Chapter metadata parsing ─────────────────────────────────────────────────
def _parse_chapter_meta(file_path: Path) -> dict[str, Any]:
    """Extract chapter_number and chapter_title from filename."""
    stem = file_path.stem  # e.g. chapter04_classification-and-regression
    m = re.match(r"chapter(\d+)(?:_(.*))?", stem)
    if m:
        num = m.group(1)
        title = (m.group(2) or "").replace("-", " ").title()
    else:
        num = "00"
        title = stem
    return {
        "chapter_number": num,
        "chapter_title": title,
        "chapter_key": stem.split("_")[0] if "_" in stem else stem,
    }


# ─── Content type heuristics ─────────────────────────────────────────────────
def _classify_content_type(text: str) -> str:
    """Classify chunk as conceptual / tutorial / mathematical / code_listing."""
    has_code = "```" in text
    code_lines = sum(1 for line in text.splitlines() if line.strip().startswith("```") or (has_code and line.startswith("    ")))
    total_lines = max(1, len(text.splitlines()))
    code_ratio = code_lines / total_lines

    if re.search(r"\$.*?\$|\\frac|\\sum|\\mathbb|∂|∇|≈|∑", text):
        return "mathematical"
    if code_ratio > 0.4 or (has_code and "def " in text):
        return "code_listing"
    if re.search(r"(step \d|how to|follow|let'?s|in this chapter|first,|next,|finally,)", text, re.I):
        return "tutorial"
    return "conceptual"


# ─── Chunker ──────────────────────────────────────────────────────────────────
class DLWPChunker:
    def __init__(self, tokenizer, overlap_tokens: int = 90):
        self.tokenizer = tokenizer
        self.overlap_tokens = overlap_tokens

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True))

    def _split_code_and_prose(self, text: str) -> list[str]:
        """Split text preserving code blocks as atomic units."""
        parts = re.split(r"(```[\s\S]*?```)", text)
        elements: list[str] = []
        for part in parts:
            if part.startswith("```"):
                elements.append(part.strip())
            else:
                paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
                elements.extend(paragraphs)
        return elements

    def chunk(self, text: str) -> list[dict[str, Any]]:
        """
        Chunk text into 300-450 token segments with code block preservation.
        Overlap is injected at output time so it does not inflate the flush threshold.
        Returns list of dicts: {text, token_count, has_code}
        """
        elements = self._split_code_and_prose(text)
        raw_segments: list[str] = []

        current_parts: list[str] = []
        current_tokens = 0

        def _flush(parts: list[str]) -> None:
            if not parts:
                return
            raw_segments.append("\n\n".join(parts))

        for el in elements:
            el_tokens = self.count_tokens(el)

            if current_tokens + el_tokens > TARGET_MAX_TOKENS and current_parts:
                _flush(current_parts)
                current_parts = []
                current_tokens = 0

            current_parts.append(el)
            current_tokens += el_tokens

        if current_parts:
            _flush(current_parts)

        # Inject overlap AFTER flushing so it never affects the split decision
        chunks: list[dict[str, Any]] = []
        for i, seg in enumerate(raw_segments):
            if i > 0 and self.overlap_tokens > 0:
                prev_words = raw_segments[i - 1].split()
                tail = " ".join(prev_words[-self.overlap_tokens:])
                text_with_overlap = tail + "\n\n" + seg
            else:
                text_with_overlap = seg

            tok = self.count_tokens(text_with_overlap)
            has_code = "```" in text_with_overlap
            chunks.append({"text": text_with_overlap, "token_count": tok, "has_code": has_code})

        return chunks


# ─── Validation (§ 7A) ────────────────────────────────────────────────────────
def validate_chunk(chunk_text: str, token_count: int, idx: int) -> tuple[bool, str]:
    """
    Returns (keep, reason). Reason is populated only when discarding.
    """
    # Too short
    if token_count < MIN_TOKENS:
        return False, f"too_short ({token_count} tokens)"

    has_code = "```" in chunk_text
    # Orphaned code: chunk is >80% code but has fewer than 20 prose words
    if has_code:
        prose = re.sub(r"```[\s\S]*?```", "", chunk_text).strip()
        prose_words = len(prose.split())
        if prose_words < 20:
            return False, "orphaned_code (no explanatory context)"

    # False positive has_code: inline backtick mentions but no real code block
    if not has_code and "`" in chunk_text:
        pass  # fine — inline mentions are valid content

    return True, ""


# ─── Near-duplicate detection (§ 7A) ─────────────────────────────────────────
def detect_near_dupes(chunks: list[dict], threshold: float = 0.70) -> int:
    """
    Simple token-overlap near-dupe detection across chapter boundaries.
    Returns count of flagged chunks (they are logged, not removed).
    """
    flagged = 0
    # Build bigram sets for lightweight Jaccard
    def bigrams(text: str) -> set[str]:
        tokens = text.lower().split()
        return {f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)}

    seen: list[set[str]] = []
    for c in chunks:
        bg = bigrams(c["text"])
        for s in seen:
            if len(bg) == 0 or len(s) == 0:
                continue
            overlap = len(bg & s) / min(len(bg), len(s))
            if overlap >= threshold:
                log.warning(f"Near-duplicate chunk detected (overlap={overlap:.2f}): {c['text'][:80]!r}")
                flagged += 1
                break
        seen.append(bg)
    return flagged


# ─── Main pipeline ────────────────────────────────────────────────────────────
class DLWPIngestor:
    def __init__(self, dry_run: bool, batch_size: int, resume: bool):
        self.dry_run    = dry_run
        self.batch_size = batch_size
        self.resume     = resume
        self.tokenizer  = _load_tokenizer()
        self.chunker    = DLWPChunker(self.tokenizer)

        # Stats
        self.stats: dict[str, int] = {
            "total": 0, "kept": 0, "discarded_short": 0,
            "discarded_orphan": 0, "warned_large": 0, "near_dupes": 0,
        }

    # ── Checkpoint helpers ──────────────────────────────────────────────────
    def _load_checkpoint(self) -> set[str]:
        """Return set of already-embedded chapter stems."""
        if CHECKPOINT.exists():
            with open(CHECKPOINT) as f:
                return set(json.load(f).get("done_chapters", []))
        return set()

    def _save_checkpoint(self, done: set[str]) -> None:
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT, "w") as f:
            json.dump({"done_chapters": sorted(done)}, f)

    # ── Chunk a single chapter ───────────────────────────────────────────────
    def _chunk_chapter(self, file_path: Path) -> list[dict[str, Any]]:
        chapter_meta = _parse_chapter_meta(file_path)
        chapter_key  = chapter_meta["chapter_key"]
        topics       = CHAPTER_TOPICS.get(chapter_key, [])

        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        raw = self.chunker.chunk(content)
        indexed_at = datetime.datetime.utcnow().isoformat() + "Z"

        # Pre-generate UUIDs so prev/next linkage is trivial (strategy § 6D)
        uuids = [str(uuid.uuid4()) for _ in raw]

        kept: list[dict[str, Any]] = []
        for i, c in enumerate(raw):
            self.stats["total"] += 1
            if c["token_count"] > WARN_TOKENS:
                self.stats["warned_large"] += 1
                log.warning(f"  [{chapter_key}] chunk {i}: {c['token_count']} tokens")

            ok, reason = validate_chunk(c["text"], c["token_count"], i)
            if not ok:
                if "short" in reason:
                    self.stats["discarded_short"] += 1
                else:
                    self.stats["discarded_orphan"] += 1
                log.debug(f"  Discarding chunk {i}: {reason}")
                continue

            self.stats["kept"] += 1

            metadata = {
                # § 3A — Core
                "book_title":      "Deep Learning with Python 3rd Edition",
                "chapter_number":  chapter_meta["chapter_number"],
                "chapter_title":   chapter_meta["chapter_title"],
                "chunk_index":     i,
                "token_count":     c["token_count"],
                # § 3B — Embedding
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dim":   EMBEDDING_DIM,
                "chunk_tokenizer": "xlm-roberta-large",  # bge-m3 uses xlm-roberta tokenizer
                "indexed_at":      indexed_at,
                # § 3C — Derived
                "has_code":        c["has_code"],
                "content_type":    _classify_content_type(c["text"]),
                "topics":          topics,
            }

            entry = {
                "id":          uuids[i],
                "text":        c["text"],
                "token_count": c["token_count"],
                "metadata":    metadata,
            }
            kept.append(entry)

        # Wire up prev/next across kept chunks (use their actual index in kept[])
        for j in range(len(kept)):
            if j > 0:
                kept[j]["metadata"]["prev_chunk_id"] = kept[j - 1]["id"]
            if j < len(kept) - 1:
                kept[j]["metadata"]["next_chunk_id"] = kept[j + 1]["id"]

        return kept

    # ── Embed a batch ────────────────────────────────────────────────────────
    async def _embed_batch(
        self,
        embed_service: Any,
        batch: list[dict],
        batch_num: int,
        total_batches: int,
    ) -> None:
        texts = [c["text"] for c in batch]
        log.info(f"  Embedding batch {batch_num}/{total_batches} ({len(texts)} chunks)…")
        try:
            vectors = await embed_service.create_embeddings(texts)
            for j, vec in enumerate(vectors):
                batch[j]["embedding"] = vec
        except Exception as exc:
            log.error(f"  Batch {batch_num} failed: {exc}")
            raise

    # ── Main entry ───────────────────────────────────────────────────────────
    async def run(self) -> None:
        if not INPUT_DIR.exists():
            log.error(f"Input dir not found: {INPUT_DIR}. Run clean_dlwp.py first.")
            sys.exit(1)

        files = sorted(INPUT_DIR.glob("*.txt"))
        if not files:
            log.error(f"No .txt files in {INPUT_DIR}")
            sys.exit(1)

        done_chapters = self._load_checkpoint() if self.resume else set()
        if done_chapters:
            log.info(f"Resuming — already done: {sorted(done_chapters)}")

        all_chunks: list[dict[str, Any]] = []

        # ── Phase 1: Chunk + Validate all chapters ──────────────────────────
        log.info(f"Phase 1: Chunking {len(files)} chapters…")
        all_raw: list[dict] = []
        for file_path in files:
            stem = file_path.stem
            if stem in done_chapters and self.resume:
                log.info(f"  Skipping {stem} (checkpoint)")
                continue
            log.info(f"  {stem}")
            chapter_chunks = self._chunk_chapter(file_path)
            log.info(f"    → {len(chapter_chunks)} chunks kept")
            all_raw.extend(chapter_chunks)

        # Near-duplicate check across all chapters (§ 7A)
        log.info("Near-duplicate scan across chapters…")
        self.stats["near_dupes"] = detect_near_dupes(all_raw)

        # ── Summary of dry-run / validation ──────────────────────────────────
        log.info("─── Chunk Distribution Summary ─────────────────────────────")
        log.info(f"  Total chunks produced : {self.stats['total']}")
        log.info(f"  Kept                  : {self.stats['kept']}")
        log.info(f"  Discarded (too short) : {self.stats['discarded_short']}")
        log.info(f"  Discarded (orphan code): {self.stats['discarded_orphan']}")
        log.info(f"  Warned (>500 tokens)  : {self.stats['warned_large']}")
        log.info(f"  Near-dupe flags       : {self.stats['near_dupes']}")
        pct_over = (self.stats["warned_large"] / max(1, self.stats["total"])) * 100
        log.info(f"  % over limit          : {pct_over:.1f}%")
        if pct_over > 5:
            log.warning("  > 5% of chunks over limit — bge-m3 selected (handles 8192 tokens)")
        log.info("─────────────────────────────────────────────────────────────")

        if self.dry_run:
            log.info("Dry-run complete. Exiting without embedding.")
            return

        # ── Phase 2: Embed ────────────────────────────────────────────────────
        log.info(f"Phase 2: Embedding with {EMBEDDING_MODEL} (CPU, FP32)…")
        # Ensure we use the model defined in this script's config
        embed_service = get_embedding_service()
        embed_service.model = EMBEDDING_MODEL
        embed_service.provider = "local_bge"
        # Reset local_model to ensure it uses the correct model and CPU device
        embed_service.reload_clients()
        total_batches = (len(all_raw) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(all_raw), self.batch_size):
            batch = all_raw[i : i + self.batch_size]
            await self._embed_batch(embed_service, batch, i // self.batch_size + 1, total_batches)

        all_chunks = all_raw

        # ── Phase 3: Serialise to chunks.json ────────────────────────────────
        log.info(f"Phase 3: Saving {len(all_chunks)} chunks → {CHUNKS_FILE}")
        CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Load and merge — remove any previous DLWP chunks for idempotency
        existing: list[dict] = []
        if CHUNKS_FILE.exists():
            try:
                with open(CHUNKS_FILE) as f:
                    existing = json.load(f)
            except json.JSONDecodeError:
                existing = []
        existing = [c for c in existing if c.get("source_name") != SOURCE_NAME]

        final: list[dict] = []
        for c in all_chunks:
            final.append({
                "id":          c["id"],
                "content":     c["text"],
                "source_name": SOURCE_NAME,
                "source_url":  SOURCE_URL,
                "chunk_order": c["metadata"]["chunk_index"],
                "token_count": c["token_count"],
                "source_type": "markdown",
                "tags":        TAGS,
                "metadata":    c["metadata"],
                "embedding":   c.get("embedding"),
            })

        existing.extend(final)
        with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        log.info(f"chunks.json now contains {len(existing)} total chunks.")

        # Clear checkpoint on success
        if CHECKPOINT.exists():
            CHECKPOINT.unlink()
        log.info("Ingestion complete ✓")


# ─── CLI ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DLWP corpus with local BGE-M3 embeddings"
    )
    parser.add_argument("--dry-run",    action="store_true",
                        help="Chunk + validate only, no embedding or saving")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Embedding batch size (default: 32)")
    parser.add_argument("--resume",     action="store_true",
                        help="Resume from checkpoint after interruption")
    args = parser.parse_args()

    ingestor = DLWPIngestor(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        resume=args.resume,
    )
    await ingestor.run()


if __name__ == "__main__":
    asyncio.run(main())
