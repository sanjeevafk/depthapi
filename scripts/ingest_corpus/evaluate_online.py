"""
evaluate_online.py — Production-grade RAG retrieval benchmark against live Supabase.
Queries the hybrid_search_v5 RPC and validates against stable content hashes.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from collections import Counter
import sys

# Ensure we can import from the root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import get_supabase_admin
from api.services.rag.embeddings import get_embedding_service
from api.services.rag.reranker import get_reranker_service

# Constants
EVAL_DIR = Path("evaluation")
QUERIES_FILE = EVAL_DIR / "queries.json"
GROUND_TRUTH_FILE = EVAL_DIR / "ground_truth.json"
API_KEY_ID = "11111111-1111-1111-1111-111111111111"


def _normalize_hash(value: str) -> str:
    if not value:
        return ""
    cleaned = "".join(ch for ch in value.lower() if ch.isalnum())
    return cleaned[:16]

async def evaluate_query(
    supabase,
    embed_service,
    reranker_service,
    query_text,
    relevant_hashes,
    api_key_id,
    top_k=20,
    rerank=False,
):
    # 1. Embed query
    query_embedding = await embed_service.create_embeddings([query_text])
    embedding_list = query_embedding[0]

    # 2. Call Hybrid Search RPC
    # If reranking, we fetch more candidates to re-sort (e.g., 50)
    pool_size = 50 if rerank else top_k
    resp = await supabase.rpc("hybrid_search_v5", {
        "query_text": query_text,
        "query_embedding": embedding_list,
        "target_api_key_id": api_key_id,
        "final_count": pool_size
    }).execute()

    if resp.error:
        print(f"Error searching for query: {resp.error}")
        return []

    hits = resp.data or []
    if not hits:
        return []

    # 3. Apply Reranking if requested
    if rerank and reranker_service:
        hits = await reranker_service.rerank(query_text, hits, top_n=top_k)
    
    # 4. Fetch content_hashes for the results
    hit_ids = [h["chunk_id"] for h in hits]
    hash_resp = await supabase.table("knowledge_chunks").select("id, content_hash").in_("id", hit_ids).execute()
    
    if not hash_resp.data:
        return []
        
    id_to_hash = {str(r["id"]): _normalize_hash(r["content_hash"]) for r in hash_resp.data}
    retrieved_hashes = [id_to_hash.get(str(h["chunk_id"])) for h in hits if str(h["chunk_id"]) in id_to_hash]
    
    matches = set(retrieved_hashes) & relevant_hashes
    if matches:
        print(f"    -> Match found: {len(matches)} relevant chunks retrieved!")
        
    return retrieved_hashes

def mrr(retrieved_hashes, relevant_hashes):
    for i, h in enumerate(retrieved_hashes):
        if h in relevant_hashes:
            return 1.0 / (i + 1)
    return 0.0

def hit_rate_at_k(retrieved_hashes, relevant_hashes, k):
    retrieved_k = retrieved_hashes[:k]
    for h in retrieved_k:
        if h in relevant_hashes:
            return 1.0
    return 0.0

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", nargs="+", type=int, default=[5, 10, 20])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--rerank", action="store_true", help="Enable Stage-2 Reranking with Cross-Encoder")
    parser.add_argument("--api-key-id", type=str, default=API_KEY_ID)
    args = parser.parse_args()

    supabase = get_supabase_admin()
    embed = get_embedding_service()
    reranker = get_reranker_service() if args.rerank else None
    
    with QUERIES_FILE.open() as f:
        queries = json.load(f)
    with GROUND_TRUTH_FILE.open() as f:
        ground_truth = json.load(f)

    mode_str = "WITH RERANKING" if args.rerank else "HYBRID ONLY"
    print(f"\nStarting Online Evaluation [{mode_str}] against Supabase (API Key: {args.api_key_id})")
    print("=" * 60)

    results = {k: {"mrr": [], "hit_rate": []} for k in args.top_k}
    per_query = []

    max_k = max(args.top_k)
    for q_entry in queries:
        qid = q_entry["id"]
        text = q_entry["query"]
        relevant = {_normalize_hash(h) for h in ground_truth.get(qid, [])}
        relevant = {h for h in relevant if h}
        
        if not relevant: continue
        
        retrieved = await evaluate_query(
            supabase,
            embed,
            reranker,
            text,
            relevant,
            args.api_key_id,
            top_k=max_k,
            rerank=args.rerank,
        )
        
        q_res = {"id": qid, "hits": {}}
        for k in args.top_k:
            hr = hit_rate_at_k(retrieved, relevant, k)
            m = mrr(retrieved, relevant) if hr > 0 else 0.0 # MRR is technically rank-based, this is a simplification
            
            results[k]["hit_rate"].append(hr)
            results[k]["mrr"].append(mrr(retrieved, relevant))
            q_res["hits"][k] = int(hr)
            
        per_query.append(q_res)
        if args.verbose:
            status = " / ".join(str(q_res["hits"][k]) for k in args.top_k)
            print(f"  {qid} [{status}] {text[:50]}...")

    print("\n" + "=" * 60)
    summary = {}
    for k in args.top_k:
        hr = sum(results[k]["hit_rate"]) / len(results[k]["hit_rate"])
        mr = sum(results[k]["mrr"]) / len(results[k]["mrr"])
        summary[f"@{k}"] = {"HitRate": hr, "MRR": mr}
        bar = "█" * int(hr * 20)
        print(f"  @K={k:2d} | HitRate: {hr:.4f} {bar} | MRR: {mr:.4f}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
