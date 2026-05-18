"""
audit_release.py — Validate local datasets against the release manifest.

Checks which datasets are marked for redistribution and identifies
blacklisted files that must be excluded from the Hugging Face export.
"""

import json
import os
import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "scripts" / "release" / "release_manifest.json"
DATASETS_ROOT = REPO_ROOT / "datasets"

def load_manifest():
    with open(MANIFEST_PATH, "r") as f:
        return json.load(f)

def audit():
    manifest = load_manifest()
    ds_config = manifest.get("datasets", {})
    
    print("\n" + "="*60)
    print("  HUGGING FACE DATASET RELEASE AUDIT")
    print("="*60)
    
    total_safe = 0
    total_blocked = 0
    
    for ds_name, config in ds_config.items():
        ds_path = DATASETS_ROOT / ds_name
        status = config.get("redistributable")
        
        if not ds_path.exists():
            # Skip if doesn't exist locally, but still report
            continue

        if status is True:
            print(f"✅ [SAFE]    {ds_name:<30} | License: {config['license']}")
            total_safe += 1
        elif status is False:
            print(f"❌ [BLOCKED] {ds_name:<30} | Reason:  {config['license']}")
            total_blocked += 1
        elif status == "mixed":
            print(f"⚠️  [MIXED]   {ds_name:<30} | Requires Filtering")
            
            # Check for blacklisted files
            blacklist = config.get("blacklist", [])
            found_blacklisted = []
            
            for root, _, files in os.walk(ds_path):
                for filename in files:
                    for pattern in blacklist:
                        if fnmatch.fnmatch(filename, pattern):
                            found_blacklisted.append(filename)
            
            if found_blacklisted:
                print(f"    - Found {len(found_blacklisted)} blacklisted files to strip:")
                for f in found_blacklisted[:5]:
                    print(f"      * {f}")
                if len(found_blacklisted) > 5:
                    print(f"      * ... and {len(found_blacklisted)-5} more")
            else:
                print("    - No blacklisted files found in current local copy.")
    
    print("="*60)
    print(f"Summary: {total_safe} Safe, {total_blocked} Blocked datasets identified.")
    print("Run this audit before executing 'export_to_hf.py'.\n")

if __name__ == "__main__":
    audit()
