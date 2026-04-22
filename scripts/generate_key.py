#!/usr/bin/env python3
"""
DepthAPI — Admin API Key Generator

Usage:
    python scripts/generate_key.py --project "My App" --email "dev@example.com" --plan starter

This script generates a cryptographically secure API key, hashes it with SHA-256,
and inserts the record into the Supabase api_keys table.

The raw key is printed ONCE and never stored. Copy it immediately.

Requirements:
    - SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env or environment.
    - Run from the repo root: python scripts/generate_key.py ...
"""

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))


def _load_env() -> None:
    """Load .env from repo root if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass  # dotenv optional; env vars may already be set


def generate_raw_key() -> str:
    """Generate a 32-byte (64 hex char) cryptographically secure API key."""
    token = secrets.token_hex(32)
    return f"sk-depth-{token}"


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.strip().encode()).hexdigest()


PLAN_BUDGETS = {
    "free":       100_000,
    "starter":  2_000_000,
    "pro":     10_000_000,
    "enterprise":        0,
}

PLAN_RPM = {
    "free":       10,
    "starter":    60,
    "pro":       300,
    "enterprise":  0,
}


def insert_key(
    *,
    raw_key: str,
    project_name: str,
    owner_email: str,
    plan: str,
    monthly_token_budget: int | None,
    requests_per_minute: int | None,
) -> str:
    """Insert the hashed key into Supabase. Returns the new row UUID."""
    from supabase import create_client

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_secret = os.environ.get("SUPABASE_SECRET_KEY", "")

    if not supabase_url or not supabase_secret:
        print(
            "\n❌  SUPABASE_URL or SUPABASE_SECRET_KEY not set.\n"
            "    Export them or add them to your .env file.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    client = create_client(supabase_url, supabase_secret)

    key_hash = hash_key(raw_key)
    prefix = raw_key[:16]  # "sk-depth-a1b2c3d" — safe to display

    record = {
        "key_hash": key_hash,
        "prefix": prefix,
        "project_name": project_name,
        "owner_email": owner_email,
        "plan": plan,
        "monthly_token_budget": monthly_token_budget or PLAN_BUDGETS[plan],
        "requests_per_minute": requests_per_minute or PLAN_RPM[plan],
        "is_active": True,
    }

    response = client.table("api_keys").insert(record).execute()
    data = getattr(response, "data", None)
    if not data or not isinstance(data, list) or not data[0].get("id"):
        print(f"\n❌  Insert failed. Response: {response}\n", file=sys.stderr)
        sys.exit(1)

    return str(data[0]["id"])


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="Generate a DepthAPI API key")
    parser.add_argument("--project", required=True, help="Project name, e.g. 'My SaaS'")
    parser.add_argument("--email", required=True, help="Owner email")
    parser.add_argument(
        "--plan",
        choices=["free", "starter", "pro", "enterprise"],
        default="free",
        help="Plan tier (default: free)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Override monthly token budget (default: plan default)",
    )
    parser.add_argument(
        "--rpm",
        type=int,
        default=None,
        help="Override requests-per-minute (default: plan default)",
    )
    args = parser.parse_args()

    raw_key = generate_raw_key()
    row_id = insert_key(
        raw_key=raw_key,
        project_name=args.project,
        owner_email=args.email,
        plan=args.plan,
        monthly_token_budget=args.budget,
        requests_per_minute=args.rpm,
    )

    print("\n" + "=" * 60)
    print("  DepthAPI Key Generated Successfully")
    print("=" * 60)
    print(f"  Project       : {args.project}")
    print(f"  Email         : {args.email}")
    print(f"  Plan          : {args.plan}")
    print(f"  Key ID        : {row_id}")
    print(f"  Prefix        : {raw_key[:16]}...")
    print()
    print(f"  RAW KEY (copy now — shown once only):")
    print()
    print(f"    {raw_key}")
    print()
    print("  ⚠️  This key is NOT stored anywhere. Copy it now.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
