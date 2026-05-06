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
from api.services.embeddings import get_embedding_service

# Constants
EVAL_DIR = Path("evaluation")
QUERIES_FILE = EVAL_DIR / "queries.json"
GROUND_TRUTH_FILE = EVAL_DIR / "ground_truth.json"
API_KEY_ID = "11111111-1111-1111-1111-111111111111"

async def evaluate_query(supabase, embed_service, query_text, relevant_hashes, top_k=20):
    # 1. Embed query
    query_embedding = await embed_service.create_embeddings([query_text])
    embedding_list = query_embedding[0]

    # 2. Call Hybrid Search RPC
    # hybrid_search_v5(query_text, query_embedding, target_api_key_id, query_mode, pool_size, count, min_sim)
    resp = await supabase.rpc("hybrid_search_v5", {
        "query_text": query_text,
        "query_embedding": embedding_list,
        "target_api_key_id": API_KEY_ID,
        "final_count": top_k
    }).execute()

    if resp.error:
        print(f"Error searching for query: {resp.error}")
        return []

    hits = resp.data or []
    
    # 3. Fetch content_hashes for the results (since hybrid_search_v5 doesn't return them directly)
    if not hits:
        return []
        
    hit_ids = [h["chunk_id"] for h in hits]
    hash_resp = await supabase.table("knowledge_chunks").select("id, content_hash").in_("id", hit_ids).execute()
    
    if not hash_resp.data:
        return []
        
    id_to_hash = {str(r["id"]): r["content_hash"] for r in hash_resp.data}
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
    args = parser.parse_args()

    supabase = get_supabase_admin()
    embed = get_embedding_service()
    
    with QUERIES_FILE.open() as f:
        queries = json.load(f)
    with GROUND_TRUTH_FILE.open() as f:
        ground_truth = json.load(f)

    print(f"\nStarting Online Evaluation against Supabase (API Key: {API_KEY_ID})")
    print("=" * 60)

    results = {k: {"mrr": [], "hit_rate": []} for k in args.top_k}
    per_query = []

    max_k = max(args.top_k)
    for q_entry in queries:
        qid = q_entry["id"]
        text = q_entry["query"]
        relevant = set(ground_truth.get(qid, []))
        
        if not relevant: continue
        
        retrieved = await evaluate_query(supabase, embed, text, relevant, top_k=max_k)
        
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
