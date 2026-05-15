#!/usr/bin/env python3
"""
scripts/turso/sync_platform.py
Turso Distributed Retrieval Platform — Sync Engine

Syncs knowledge_chunks from Supabase (primary) to Turso (read replica) with:
  - Resumable cursor-based incremental sync (overlap window)
  - Adaptive batch sizing & retry backoff
  - SHA256 partition-based integrity validation
  - Tombstone propagation with ack_watermark gating
  - Observability metrics logged to stdout / Sentry

Usage:
  python scripts/turso/sync_platform.py [--full] [--validate-only]

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY
  TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import libsql_experimental as libsql  # pip install libsql-experimental
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SECRET_KEY"]  # service role key

TURSO_URL = os.environ["TURSO_DATABASE_URL"]
TURSO_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

# Sync tuning
OVERLAP_WINDOW_MINUTES = 5       # Clock-skew protection window
BATCH_SIZE_MIN = 100
BATCH_SIZE_MAX = 1000
BATCH_SIZE_INIT = 200
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2           # Exponential backoff base (seconds)

# Embedding lifecycle defaults (overridden per-row from Supabase data)
DEFAULT_EMBEDDING_MODEL = "bge-base-en-v1.5"
DEFAULT_EMBEDDING_VERSION = "v1"
DEFAULT_CHUNKING_VERSION = "v2-semantic"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("turso-sync")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SyncMetrics:
    started_at: float = field(default_factory=time.monotonic)
    rows_processed: int = 0
    rows_upserted: int = 0
    rows_tombstoned: int = 0
    rows_failed: int = 0
    rows_skipped: int = 0
    checksum_mismatches: int = 0
    retries: int = 0

    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def report(self) -> dict:
        return {
            "duration_ms": self.duration_ms(),
            "rows_processed": self.rows_processed,
            "rows_upserted": self.rows_upserted,
            "rows_tombstoned": self.rows_tombstoned,
            "rows_failed": self.rows_failed,
            "rows_skipped": self.rows_skipped,
            "checksum_mismatches": self.checksum_mismatches,
            "retries": self.retries,
        }


# ---------------------------------------------------------------------------
# Vector serialization
# ---------------------------------------------------------------------------

def embedding_to_blob(embedding) -> bytes:
    """Serialize a float32 embedding into a raw BLOB.
    Handles all formats from Supabase PostgREST:
      - flat list:   [0.1, 0.2, ...]
      - nested list: [[0.1, 0.2, ...]]
      - string:      "[0.1, 0.2, ...]"
    """
    if isinstance(embedding, str):
        # PostgreSQL vector returned as "[0.1, 0.2, ...]"
        try:
            # Strip brackets and split
            cleaned = embedding.strip("[]").split(",")
            embedding = [float(v.strip()) for v in cleaned if v.strip()]
        except Exception:
            embedding = json.loads(embedding)
    
    if not embedding:
        return b""
        
    if isinstance(embedding[0], list):
        embedding = embedding[0]
        
    return struct.pack(f"{len(embedding)}f", *[float(v) for v in embedding])


def blob_to_embedding(blob: bytes) -> list[float]:
    """Deserialize a float32 BLOB back into a list."""
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def compute_row_hash(row_id: str, content_hash: str) -> str:
    """SHA256(id|content_hash) per row — used for partition-level validation."""
    return hashlib.sha256(f"{row_id}|{content_hash}".encode()).hexdigest()


def compute_partition_hash(rows: list[dict]) -> str:
    """
    SHA256 of sorted (id + content_hash) concatenations within a window.
    Mirrors the integrity computation on the Supabase side for reconciliation.
    """
    sorted_items = sorted(f"{r['id']}|{r['content_hash']}" for r in rows)
    return hashlib.sha256("|".join(sorted_items).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Sync State persistence
# ---------------------------------------------------------------------------

def load_sync_cursor(turso: libsql.Connection) -> tuple[datetime, str | None, str | None]:
    """Load last sync state from Turso sync_state table."""
    try:
        # Ensure columns exist for keyset resumption (outside transaction)
        turso.execute("ALTER TABLE sync_state ADD COLUMN last_created_at TEXT")
        turso.execute("ALTER TABLE sync_state ADD COLUMN last_id TEXT")
    except Exception:
        pass # Columns likely exist

    try:
        rows = turso.execute(
            "SELECT last_synced_at, last_created_at, last_id FROM sync_state WHERE id = 1"
        ).fetchall()
        if rows:
            ts, last_created_at, last_id = rows[0]
            dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            return dt, last_created_at, last_id
    except Exception as e:
        log.warning(f"Could not read sync_state: {e}")
    
    # Default: sync last 24h
    return datetime.now(timezone.utc) - timedelta(hours=24), None, None


def save_sync_cursor(
    turso: libsql.Connection,
    cursor: datetime,
    metrics: SyncMetrics,
    last_created_at: str | None = None,
    last_id: str | None = None,
) -> None:
    """Persist the sync cursor and run stats into Turso."""
    turso.execute(
        """
        INSERT INTO sync_state (
            id, last_synced_at, last_run_at, 
            rows_processed, rows_failed, checksum_mismatches,
            last_created_at, last_id
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_synced_at = excluded.last_synced_at,
            last_run_at    = excluded.last_run_at,
            rows_processed = sync_state.rows_processed + excluded.rows_processed,
            rows_failed    = sync_state.rows_failed    + excluded.rows_failed,
            checksum_mismatches = sync_state.checksum_mismatches + excluded.checksum_mismatches,
            last_created_at = excluded.last_created_at,
            last_id = excluded.last_id
        """,
        (
            cursor.isoformat(),
            datetime.now(timezone.utc).isoformat(),
            metrics.rows_processed,
            metrics.rows_failed,
            metrics.checksum_mismatches,
            last_created_at,
            last_id,
        ),
    )


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def fetch_page_from_supabase(
    supabase: Client,
    since: str,
    limit: int,
    last_created_at: str | None = None,
    last_id: str | None = None,
) -> list[dict]:
    """
    Paginated fetch from Supabase with Keyset Pagination (created_at, id).
    This avoids the performance and offset limits of standard pagination.
    """
    query = supabase.table("knowledge_chunks").select(
        "id, document_id, content, metadata, embedding, content_hash, "
        "created_at, deleted_at"
    )

    if last_created_at and last_id:
        # Keyset logic: (created_at > last) OR (created_at == last AND id > last)
        query = query.or_(
            f"created_at.gt.{last_created_at},"
            f"and(created_at.eq.{last_created_at},id.gt.{last_id})"
        )
    else:
        query = query.gte("created_at", since)

    result = (
        query.order("created_at", desc=False)
        .order("id", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def upsert_batch(
    turso: libsql.Connection,
    rows: list[dict],
    metrics: SyncMetrics,
) -> None:
    """Batch UPSERT rows into Turso with integrity hash computation."""
    for row in rows:
            metrics.rows_processed += 1

            # Determine if this is a tombstone (soft delete propagation)
            is_deleted = 1 if row.get("deleted_at") else 0

            # Serialize embedding (skip if tombstoning)
            emb_blob = b""
            emb_dim = 0
            if not is_deleted and row.get("embedding"):
                try:
                    emb_blob = embedding_to_blob(row["embedding"])
                    emb_dim = len(row["embedding"])
                except Exception as e:
                    log.warning(f"Embedding serialization failed for {row['id']}: {e}")
                    metrics.rows_failed += 1
                    continue

            # Compute per-row integrity hash
            sync_hash = compute_row_hash(row["id"], row.get("content_hash", ""))

            # Extract structured metadata from JSONB
            meta = row.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            turso.execute(
                """
                INSERT INTO knowledge_chunks_platform (
                    id, document_id, content,
                    tenant_id, topic, document_type, source, language,
                    aux_metadata,
                    embedding, embedding_dim,
                    embedding_model, embedding_version, chunking_version,
                    content_hash, sync_hash,
                    created_at, updated_at, synced_at,
                    is_deleted, deleted_at
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, CURRENT_TIMESTAMP,
                    ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    content          = excluded.content,
                    aux_metadata     = excluded.aux_metadata,
                    embedding        = excluded.embedding,
                    embedding_dim    = excluded.embedding_dim,
                    embedding_model  = excluded.embedding_model,
                    embedding_version = excluded.embedding_version,
                    chunking_version = excluded.chunking_version,
                    content_hash     = excluded.content_hash,
                    sync_hash        = excluded.sync_hash,
                    updated_at       = excluded.updated_at,
                    synced_at        = CURRENT_TIMESTAMP,
                    is_deleted       = excluded.is_deleted,
                    deleted_at       = excluded.deleted_at
                """,
                (
                    row["id"],
                    row["document_id"],
                    row["content"],
                    meta.get("tenant_id", "default"),
                    meta.get("topic"),
                    meta.get("document_type"),
                    meta.get("source"),
                    meta.get("language", "en"),
                    json.dumps({k: v for k, v in meta.items()
                                if k not in {"tenant_id", "topic", "document_type", "source", "language"}}),
                    emb_blob,
                    emb_dim,
                    meta.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
                    meta.get("embedding_version", DEFAULT_EMBEDDING_VERSION),
                    meta.get("chunking_version", DEFAULT_CHUNKING_VERSION),
                    row.get("content_hash", ""),
                    sync_hash,
                    row.get("created_at"),
                    row.get("created_at"),  # no updated_at on source — use created_at
                    is_deleted,
                    row.get("deleted_at"),
                ),
            )

            if is_deleted:
                metrics.rows_tombstoned += 1
            else:
                metrics.rows_upserted += 1


def validate_partition(
    supabase: Client,
    turso: libsql.Connection,
    since: datetime,
    until: datetime,
    metrics: SyncMetrics,
) -> bool:
    """
    SHA256 partition-based integrity check for a time window.
    Returns True if hashes match (no divergence).
    """
    since_s = since.isoformat()
    until_s = until.isoformat()

    # Fetch source hashes (use created_at — no updated_at on knowledge_chunks)
    src = (
        supabase.table("knowledge_chunks")
        .select("id, content_hash")
        .gte("created_at", since_s)
        .lt("created_at", until_s)
        .is_("deleted_at", "null")
        .execute()
    ).data or []

    # Fetch replica hashes
    replica_rows = turso.execute(
        "SELECT id, content_hash FROM knowledge_chunks_platform "
        "WHERE created_at >= ? AND created_at < ? AND is_deleted = 0",
        (since_s, until_s),
    ).fetchall()
    replica = [{"id": r[0], "content_hash": r[1]} for r in replica_rows]

    src_hash = compute_partition_hash(src)
    rep_hash = compute_partition_hash(replica)

    if src_hash != rep_hash:
        log.warning(
            f"Partition mismatch [{since_s} → {until_s}]: "
            f"source={src_hash[:12]}… replica={rep_hash[:12]}… "
            f"(rows: src={len(src)}, replica={len(replica)})"
        )
        metrics.checksum_mismatches += 1
        return False

    log.info(f"Partition [{since_s} → {until_s}]: OK ({len(src)} rows)")
    return True


# ---------------------------------------------------------------------------
# Tombstone cleanup (watermark-gated)
# ---------------------------------------------------------------------------

def purge_acknowledged_tombstones(turso: libsql.Connection) -> int:
    """
    Purge tombstones only when ack_watermark is set (confirmed by integrity pass).
    Never purge based on age alone.
    """
    result = turso.execute(
        """
        DELETE FROM knowledge_chunks_platform
        WHERE is_deleted = 1
          AND ack_watermark IS NOT NULL
          AND ack_watermark != ''
        """
    )
    turso.commit()
    count = result.rowcount if hasattr(result, "rowcount") else 0
    if count:
        log.info(f"Purged {count} acknowledged tombstones.")
    return count


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def get_turso_connection() -> libsql.Connection:
    """Create a persistent connection to Turso."""
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise ValueError("Missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN")
    
    # We use isolation_level=None to disable implicit transactions (Autocommit mode)
    # This allows us to use explicit BEGIN/COMMIT blocks for reliability.
    return libsql.connect(url, auth_token=token, isolation_level=None)


def run_sync(full_sync: bool = False, validate_only: bool = False) -> SyncMetrics:
    metrics = SyncMetrics()

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    # autocommit=True: libsql-experimental is always autocommit at the protocol
    # level; this flag makes it explicit and prevents the driver from issuing
    # implicit BEGIN statements that would cause "nested transaction" errors.
    turso = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN, autocommit=True)

    # Load sync cursor
    if full_sync:
        # Sync from the beginning of time
        cursor = datetime(2020, 1, 1, tzinfo=timezone.utc)
        last_created_at = None
        last_id = None
        log.info("Full sync requested — fetching all records.")
    else:
        cursor, last_created_at, last_id = load_sync_cursor(turso)

    # For full sync, start from a time far in the past
    since = cursor - timedelta(minutes=OVERLAP_WINDOW_MINUTES)
    now = datetime.now(timezone.utc)
    log.info(f"Sync window: {since.isoformat()} → {now.isoformat()}")

    if validate_only:
        # Run integrity validation for the last 24h only
        validate_partition(supabase, turso, now - timedelta(hours=24), now, metrics)
        log.info(f"Validation complete: {metrics.report()}")
        return metrics

    # Keyset pagination with adaptive batching
    batch_size = BATCH_SIZE_INIT
    error_streak = 0

    while True:
        page: list[dict[str, Any]] = []

        for attempt in range(RETRY_ATTEMPTS):
            try:
                page = fetch_page_from_supabase(
                    supabase, since.isoformat(), batch_size,
                    last_created_at=last_created_at,
                    last_id=last_id
                )
                error_streak = 0
                break
            except Exception as e:
                metrics.retries += 1
                wait = RETRY_BACKOFF_BASE ** attempt
                log.warning(f"Fetch failed (attempt {attempt + 1}): {e}. Retrying in {wait}s.")
                time.sleep(wait)
        else:
            log.error(f"Failed to fetch page after {RETRY_ATTEMPTS} attempts. Stopping.")
            break

        if not page:
            log.info(f"Sync complete. Total processed: {metrics.rows_processed}")
            break

        # --- Phase 1: Write the data rows (autocommit; each execute is atomic) ---
        batch_ok = False
        try:
            upsert_batch(turso, page, metrics)
            batch_ok = True
        except Exception as e:
            error_streak += 1
            batch_size = max(BATCH_SIZE_MIN, batch_size // 2)
            log.warning(f"Batch upsert failed (size→{batch_size}): {e}")
            time.sleep(1)

        if not batch_ok:
            continue

        # --- Phase 2: Advance cursor ALWAYS on success (separate from checkpoint) ---
        last_created_at = page[-1]["created_at"]
        last_id = page[-1]["id"]
        error_streak = 0
        metrics.rows_processed += len(page)

        log.info(
            f"Batch OK — upserted={metrics.rows_upserted} "
            f"cursor={last_created_at}"
        )

        # --- Phase 3: Checkpoint (non-fatal; cursor already advanced in memory) ---
        try:
            save_sync_cursor(turso, since, metrics, last_created_at, last_id)
        except Exception as e:
            log.warning(f"Checkpoint save failed (non-fatal): {e}")

        # Adaptive batch sizing
        if error_streak == 0 and batch_size < BATCH_SIZE_MAX:
            batch_size = min(BATCH_SIZE_MAX, int(batch_size * 1.5))

    # Save final cursor
    try:
        save_sync_cursor(turso, now, metrics, last_created_at, last_id)
    except Exception as e:
        log.warning(f"Final checkpoint save failed: {e}")

    # Run partition validation for the synced window
    log.info("Running post-sync integrity validation...")
    validate_partition(supabase, turso, since, now, metrics)

    # Purge watermark-confirmed tombstones
    purge_acknowledged_tombstones(turso)

    report = metrics.report()
    log.info(f"Sync completed. Metrics: {json.dumps(report, indent=2)}")
    return metrics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Turso Distributed Retrieval Platform — Sync Engine")
    parser.add_argument("--full", action="store_true", help="Perform a full sync from the beginning of time.")
    parser.add_argument("--validate-only", action="store_true", help="Run integrity validation only, no writes.")
    args = parser.parse_args()

    result = run_sync(full_sync=args.full, validate_only=args.validate_only)

    if result.rows_failed > 0 or result.checksum_mismatches > 0:
        log.warning(f"Sync completed with issues: {result.rows_failed} failures, {result.checksum_mismatches} mismatches.")
        sys.exit(1)

    log.info("Sync completed successfully.")
    sys.exit(0)
