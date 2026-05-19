#!/usr/bin/env python3
"""
diagnose_supabase_namespaces.py — Analyze current table state and suggest namespace/collection renames.

Usage:
    python scripts/ingest_corpus/diagnose_supabase_namespaces.py [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import get_supabase_admin


async def diagnose() -> dict[str, Any]:
    """Query local Supabase and return diagnostic report."""
    
    supabase = get_supabase_admin()
    if not supabase:
        return {
            "error": "Supabase client unavailable",
            "postmortem_status": {"found": False, "note": "Cannot connect to Supabase"},
        }
    
    report = {
        "timestamp": None,
        "environment": "local",
        "collections": {},
        "namespaces": {},
        "chunk_stats": {},
        "postmortem_status": {},
        "recommendations": [],
        "warnings": [],
    }
    
    # ─── 1. Query knowledge_collections ───────────────────────────────────
    print("[*] Fetching knowledge_collections...")
    try:
        collections_res = await supabase.table("knowledge_collections").select("*").execute()
        collections = collections_res.data or []
        
        for coll in collections:
            coll_id = coll.get("id")
            coll_name = coll.get("name", "unnamed")
            report["collections"][coll_id] = {
                "name": coll_name,
                "api_key_id": coll.get("api_key_id"),
                "created_at": coll.get("created_at"),
                "metadata": coll.get("metadata", {}),
            }
            print(f"  ✓ {coll_name} (id: {coll_id})")
            
    except Exception as e:
        err = f"Failed to fetch collections: {e}"
        report["warnings"].append(err)
        print(f"  ✗ {err}")
        collections = []
    
    # ─── 2. Query knowledge_chunks for namespace stats ────────────────────
    print("\n[*] Analyzing knowledge_chunks table...")
    try:
        chunks_res = await supabase.table("knowledge_chunks").select(
            "id,collection_id,document_id,content_hash,metadata"
        ).execute()
        chunks = chunks_res.data or []
        
        print(f"  ✓ Total chunks in database: {len(chunks)}")
        
        # Group by collection
        by_collection = defaultdict(list)
        for chunk in chunks:
            coll_id = chunk.get("collection_id")
            by_collection[coll_id].append(chunk)
        
        # Analyze each collection
        for coll_id, coll_chunks in by_collection.items():
            coll_name = next(
                (c["name"] for c in collections if c.get("id") == coll_id),
                f"unknown_{coll_id[:8]}"
            )
            
            # Extract metadata namespaces
            ns_counter = Counter()
            source_counter = Counter()
            
            for chunk in coll_chunks:
                meta = chunk.get("metadata") or {}
                ns = meta.get("namespace", "untagged")
                ns_counter[ns] += 1
                
                source = meta.get("source_name", "unknown")
                source_counter[source] += 1
            
            report["chunk_stats"][coll_id] = {
                "collection_name": coll_name,
                "chunk_count": len(coll_chunks),
                "namespaces": dict(ns_counter),
                "top_sources": dict(source_counter.most_common(10)),
            }
            
            print(f"  | Collection: {coll_name}")
            print(f"    - Total chunks: {len(coll_chunks)}")
            print(f"    - Namespaces: {dict(ns_counter)}")
            
    except Exception as e:
        err = f"Failed to fetch chunks: {e}"
        report["warnings"].append(err)
        print(f"  ✗ {err}")
    
    # ─── 3. Analyze namespace naming patterns ──────────────────────────────
    print("\n[*] Analyzing namespace patterns...")
    
    generic_names = {"trusted", "local", "default", "system", "corpus", "depthapi", "untagged"}
    all_namespaces = set()
    
    for coll_id, stats in report.get("chunk_stats", {}).items():
        for ns in stats.get("namespaces", {}).keys():
            all_namespaces.add(ns)
    
    for ns in sorted(all_namespaces):
        total = sum(
            stats.get("namespaces", {}).get(ns, 0)
            for stats in report.get("chunk_stats", {}).values()
        )
        
        is_generic = ns.lower() in generic_names
        report["namespaces"][ns] = {
            "is_generic": is_generic,
            "total_chunks": total,
        }
        
        tag = "⚠️  GENERIC" if is_generic else "✓ data-aware"
        print(f"  {tag}: {ns:30s} ({total:6d} chunks)")
    
    # ─── 4. Find postmortem ingestion status ───────────────────────────────
    print("\n[*] Checking postmortem/post-mortem ingestion status...")
    
    pm_variations = ["postmortem", "post-mortem", "post_mortem", "post-mortems", "postmortems"]
    postmortem_chunks = defaultdict(int)
    postmortem_total = 0
    
    for coll_id, stats in report.get("chunk_stats", {}).items():
        for source, count in stats.get("top_sources", {}).items():
            if any(pm in source.lower() for pm in pm_variations):
                postmortem_chunks[source] += count
                postmortem_total += count
        
        for ns, count in stats.get("namespaces", {}).items():
            if any(pm in ns.lower() for pm in pm_variations):
                postmortem_chunks[f"namespace:{ns}"] += count
                postmortem_total += count
    
    if postmortem_total > 0:
        report["postmortem_status"]["found"] = True
        report["postmortem_status"]["distributions"] = dict(postmortem_chunks)
        report["postmortem_status"]["total_chunks"] = postmortem_total
        print(f"  ✓ Found postmortem data: {postmortem_total} chunks")
        for key, count in sorted(postmortem_chunks.items(), key=lambda x: -x[1]):
            print(f"    - {key}: {count}")
    else:
        report["postmortem_status"]["found"] = False
        report["postmortem_status"]["note"] = "No postmortem data found. Run ingest_postmortems.py to add."
        print(f"  ✗ No postmortem data found in database")
    
    # ─── 5. Also check system_design namespace (where postmortems should be) ─
    system_design_chunks = sum(
        stats.get("namespaces", {}).get("system_design", 0)
        for stats in report.get("chunk_stats", {}).values()
    )
    if system_design_chunks > 0:
        print(f"  ℹ️  system_design namespace: {system_design_chunks} chunks")
        if not postmortem_total:
            print(f"     (postmortems may be in system_design namespace)")
    
    # ─── 5. Identify single-chunk namespaces (redundant) ────────────────────
    print("\n[*] Identifying redundant single-chunk namespaces...")
    
    single_chunk_ns = [
        ns for ns, info in report["namespaces"].items()
        if info.get("total_chunks") == 1
    ]
    
    if single_chunk_ns:
        report["recommendations"].append({
            "type": "consolidate_single_chunks",
            "severity": "low",
            "description": "Merge single-chunk namespaces to reduce fragmentation",
            "affected": single_chunk_ns,
            "action": f"Consider consolidating {len(single_chunk_ns)} single-chunk namespaces into a 'miscellaneous' or related category",
        })
        print(f"  ⚠️  Found {len(single_chunk_ns)} single-chunk namespaces:")
        for ns in single_chunk_ns:
            print(f"    - {ns}")
    else:
        print(f"  ✓ No single-chunk namespaces found")
    
    # ─── 6. Recommend collection/namespace renames ──────────────────────────
    print("\n[*] Generating namespace rename recommendations...")
    
    rename_candidates = []
    for coll_id, stats in report.get("chunk_stats", {}).items():
        coll_name = stats.get("collection_name", "unknown")
        if coll_name.lower() in generic_names or "depthapi" in coll_name.lower():
            # Suggest a data-aware name based on dominant namespace/sources
            top_ns = max(
                stats.get("namespaces", {}).items(),
                key=lambda x: x[1],
                default=("trusted", 0)
            )[0]
            
            top_source = max(
                stats.get("top_sources", {}).items(),
                key=lambda x: x[1],
                default=("unknown", 0)
            )[0]
            
            suggested_name = f"Technical Corpus - {top_source}" if top_source != "unknown" else f"DepthAPI {top_ns.title()} Corpus"
            
            candidate = {
                "collection_id": coll_id,
                "current_name": coll_name,
                "suggested_name": suggested_name,
                "rationale": f"Generic name; top source is '{top_source}', top namespace is '{top_ns}'",
            }
            rename_candidates.append(candidate)
            
            print(f"  📋 {coll_name}")
            print(f"     → Suggest: {suggested_name}")
            print(f"     📌 Rationale: {candidate['rationale']}")
    
    if rename_candidates:
        report["recommendations"].append({
            "type": "rename_collections",
            "severity": "medium",
            "description": "Rename generic collection names to data-aware names",
            "candidates": rename_candidates,
        })
    
    # ─── Generate SQL rename statements ────────────────────────────────────
    print("\n[*] SQL rename statements (if needed)...")
    
    sql_statements = []
    for candidate in rename_candidates:
        old_name = candidate["current_name"].replace("'", "''")
        new_name = candidate["suggested_name"].replace("'", "''")
        stmt = f"UPDATE knowledge_collections SET name = '{new_name}' WHERE name = '{old_name}';"
        sql_statements.append(stmt)
        print(f"  {stmt}")
    
    if sql_statements:
        report["recommendations"].append({
            "type": "sql_rename_statements",
            "statements": sql_statements,
        })
    
    return report


async def main():
    parser = argparse.ArgumentParser(
        description="Diagnose Supabase namespace & collection state"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full JSON report",
    )
    
    args = parser.parse_args()
    
    print(f"[*] Connecting to local Supabase instance...")
    
    try:
        report = await diagnose()
        
        if args.json:
            print("\n" + "=" * 80)
            print("FULL JSON REPORT:")
            print("=" * 80)
            print(json.dumps(report, indent=2))
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        
        if report.get("postmortem_status", {}).get("found"):
            print(f"✅ Postmortem Status: INGESTED")
            print(f"   Total Chunks: {report['postmortem_status']['total_chunks']}")
        else:
            print(f"❌ Postmortem Status: NOT FOUND")
            print(f"   Action: Run ingest_postmortems.py to ingest data")
        
        generic_count = sum(1 for ns, info in report.get("namespaces", {}).items() if info.get("is_generic"))
        total_ns = len(report.get("namespaces", {}))
        print(f"\n📊 Namespace Analysis:")
        print(f"   ⚠️  Generic Names: {generic_count}")
        print(f"   ✓ Data-Aware Names: {total_ns - generic_count}")
        
        single_chunk_count = sum(1 for ns, info in report.get("namespaces", {}).items() if info.get("total_chunks") == 1)
        if single_chunk_count > 0:
            print(f"\n   🚨 Redundant Single-Chunk Namespaces: {single_chunk_count}")
        
        print(f"\n📋 Recommendations: {len(report.get('recommendations', []))}")
        for i, rec in enumerate(report.get("recommendations", []), 1):
            rec_type = rec.get('type', '').replace('_', ' ').title()
            severity = rec.get('severity', 'info').upper()
            desc = rec.get('description', '')
            print(f"   {i}. [{severity}] {rec_type}")
            print(f"      {desc}")
        
        return 0
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
