#!/usr/bin/env python3
"""
get_or_create_api_key.py — Get or create an API key for backfilling RAG data.
"""

import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import get_supabase_admin


async def main():
    supabase = get_supabase_admin()
    if not supabase:
        print("❌ Cannot connect to Supabase")
        return None
    
    # Check for existing API keys
    try:
        res = await supabase.table("api_keys").select("id,project_name").limit(1).execute()
        if res.data and len(res.data) > 0:
            key_id = res.data[0]["id"]
            project_name = res.data[0].get("project_name", "Unknown")
            print(f"✅ Using existing API key: {key_id}")
            print(f"   Project: {project_name}")
            return key_id
    except Exception as e:
        print(f"⚠️  Could not query existing keys: {e}")
    
    # Create new API key if none exist
    print("\n[*] Creating new API key for local development...")
    try:
        new_key_id = str(uuid.uuid4())
        raw_key = f"sk-depth-{uuid.uuid4().hex[:8]}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        prefix = raw_key[:16]
        
        res = await supabase.table("api_keys").insert({
            "id": new_key_id,
            "key_hash": key_hash,
            "prefix": prefix,
            "project_name": "Local Development - RAG Backfill",
            "owner_email": "dev@local.test",
            "plan": "free"
        }).execute()
        
        if res.error or not res.data:
            print(f"❌ Failed to create API key: {res.error}")
            return None
        
        print(f"✅ Created new API key: {new_key_id}")
        return new_key_id
        
    except Exception as e:
        print(f"❌ Error creating API key: {e}")
        return None


if __name__ == "__main__":
    key_id = asyncio.run(main())
    if key_id:
        print(f"\n📋 Use this for backfill:")
        print(f"   --api-key-id {key_id}")
        sys.exit(0)
    else:
        sys.exit(1)
