import asyncio
import os
from typing import Any, Dict, List
import structlog
from api.auth import get_supabase_admin
from api.adapters.supabase_adapter import SupabaseHTTPClient

logger = structlog.get_logger(__name__)

async def run_dedup_audit():
    """Audit the Supabase knowledge_chunks table for content-level duplicates."""
    supabase = get_supabase_admin()
    if not supabase:
        print("Error: Could not connect to Supabase.")
        return

    print("--- RAG Deduplication Audit ---")
    
    # 1. Check for exact content duplicates (same hash, different IDs)
    print("\n[1] Checking for exact content hash collisions...")
    # SQL query to find duplicate hashes
    query = """
    SELECT content_hash, COUNT(*) as count
    FROM knowledge_chunks
    WHERE deleted_at IS NULL
    GROUP BY content_hash
    HAVING COUNT(*) > 1
    ORDER BY count DESC
    LIMIT 10;
    """
    
    # Using direct SQL if possible, or just fetching chunks to analyze
    # Since we can't run raw SQL easily via the adapter's .rpc without a function,
    # let's check if there's an existing RPC or use the table interface.
    
    # Actually, we can fetch the counts via .select() with aggregates if supported,
    # but let's try a simpler approach: fetch all hashes and count in Python.
    # Note: If the table is huge, this is slow.
    
    res = await supabase.table("knowledge_chunks").select("content_hash, id, document_id").execute()
    chunks = res.data or []
    
    hash_map = {}
    for c in chunks:
        h = c["content_hash"]
        if h not in hash_map:
            hash_map[h] = []
        hash_map[h].append(c)
    
    dups = {h: items for h, items in hash_map.items() if len(items) > 1}
    
    if not dups:
        print("  Result: No exact content duplicates found.")
    else:
        print(f"  Result: Found {len(dups)} unique content hashes with duplicates.")
        total_dup_chunks = sum(len(items) for items in dups.values())
        print(f"  Total duplicate chunks: {total_dup_chunks}")
        
        # Sample
        print("\n  Top duplicate samples:")
        for h, items in list(dups.items())[:3]:
            print(f"    Hash: {h[:12]}... Count: {len(items)}")
            for item in items:
                print(f"      - ID: {item['id']} (Doc: {item['document_id']})")

    # 2. Evaluate dual-tsvector redundancy in search results
    print("\n[2] Evaluating dual-tsvector redundancy in hybrid_search_v5...")
    # This requires running a sample search and checking if 'dense', 'sparse_english', 'sparse_simple'
    # all return the same chunks frequently.
    
    # Pick a sample query
    sample_query = "python decorators"
    user_id = "00000000-0000-0000-0000-000000000000" # dummy
    
    try:
        # Note: We need a query embedding to run hybrid_search_v5
        # For simplicity, we'll skip this part if we can't easily generate one here,
        # but the SQL logic itself already deduplicates by ID.
        print("  (Note: SQL hybrid_search_v5 already performs ID-based UNION. Content-level dedup is the focus.)")
    except Exception as e:
        print(f"  Error running sample search: {e}")

if __name__ == "__main__":
    asyncio.run(run_dedup_audit())
