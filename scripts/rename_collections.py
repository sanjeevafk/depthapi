#!/usr/bin/env python3
"""
rename_collections.py — Execute collection name updates via Supabase SQL.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import get_supabase_admin


async def rename_collections():
    """Execute SQL renames for all collections."""
    
    supabase = get_supabase_admin()
    if not supabase:
        print("❌ ERROR: Cannot connect to Supabase")
        return False
    
    # Define renames: (old_id, new_name)
    renames = [
        {
            "id": "e1fea9c7-396d-4a09-a208-f0c956da884a",
            "old_name": "DepthAPI Trusted Corpus",
            "new_name": "Technical Corpus - Trusted Knowledge"
        },
        {
            "id": "51fced1a-f5e7-4873-bf5c-f2098bd3affa",
            "old_name": "DepthAPI Trusted Corpus",
            "new_name": "Technical Corpus - Postmortems & Archived"
        },
        {
            "id": "49ffa1e8-f508-42b3-bf49-6680ccddc962",
            "old_name": "DepthAPI_Trusted_Corpus",
            "new_name": "Technical Corpus - Legacy (Underscore Format)"
        }
    ]
    
    print("=" * 80)
    print("COLLECTION RENAME EXECUTION")
    print("=" * 80)
    
    all_success = True
    results = []
    
    for rename in renames:
        coll_id = rename["id"]
        old_name = rename["old_name"]
        new_name = rename["new_name"]
        
        print(f"\n[*] Renaming collection {coll_id[:8]}...")
        print(f"    Old: {old_name}")
        print(f"    New: {new_name}")
        
        try:
            # Execute the update
            response = await supabase.table("knowledge_collections").update({
                "name": new_name
            }).eq("id", coll_id).execute()
            
            if response.error:
                print(f"    ❌ FAILED: {response.error}")
                results.append({
                    "id": coll_id,
                    "old_name": old_name,
                    "new_name": new_name,
                    "status": "FAILED",
                    "error": str(response.error)
                })
                all_success = False
            else:
                print(f"    ✅ SUCCESS")
                results.append({
                    "id": coll_id,
                    "old_name": old_name,
                    "new_name": new_name,
                    "status": "SUCCESS"
                })
        except Exception as e:
            print(f"    ❌ EXCEPTION: {e}")
            results.append({
                "id": coll_id,
                "old_name": old_name,
                "new_name": new_name,
                "status": "ERROR",
                "error": str(e)
            })
            all_success = False
    
    # ─── Verify changes ───────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)
    
    try:
        verify_res = await supabase.table("knowledge_collections").select("id,name").execute()
        collections = verify_res.data or []
        
        print(f"\nCurrent collections in database ({len(collections)} total):")
        for coll in collections:
            coll_id = coll.get("id")
            coll_name = coll.get("name")
            
            # Check if this was in our rename list
            was_renamed = any(r["id"] == coll_id for r in renames)
            marker = "✅" if was_renamed else "  "
            print(f"  {marker} {coll_name} (id: {coll_id})")
        
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        all_success = False
    
    # ─── Final summary ────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    failed_count = sum(1 for r in results if r["status"] in ("FAILED", "ERROR"))
    
    print(f"\n👤 Collections Renamed: {success_count}/{len(renames)}")
    
    if success_count == len(renames):
        print("\n✅ ALL RENAMES SUCCESSFUL")
        return True
    else:
        print(f"\n⚠️  {failed_count} rename(s) failed:")
        for r in results:
            if r["status"] != "SUCCESS":
                print(f"   - {r['old_name']} → {r['new_name']}: {r.get('error', r['status'])}")
        return False


async def main():
    try:
        success = await rename_collections()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
