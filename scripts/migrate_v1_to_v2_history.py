#!/usr/bin/env python3
import argparse
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

from supabase import create_client

DEFAULT_BATCH_SIZE = 200


def parse_bool(value: str) -> bool:
    if value.lower() in {"true", "1", "yes", "y"}:
        return True
    if value.lower() in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("Expected true/false")


def get_env(name: str) -> Optional[str]:
    return os.environ.get(name)


def fetch_all_rows(table, select: str, order_col: Optional[str] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    limit = 1000
    offset = 0
    while True:
        query = table.select(select)
        if order_col:
            query = query.order(order_col, desc=False)
        response = query.range(offset, offset + limit - 1).execute()
        data = response.data or []
        if not data:
            break
        rows.extend(data)
        offset += limit
    return rows


def build_conversation_key(user_id: str, topic: str, mode: Optional[str]) -> Tuple[str, str, Optional[str]]:
    return (user_id, topic, mode)


def pick_first(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value is not None and str(value).strip() != "":
            return str(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate v1 history to v2 conversations/messages.")
    parser.add_argument("--dry-run", type=parse_bool, default=True, help="Run without writing (default: true)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_SERVICE_ROLE_KEY") or get_env("SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        logging.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY/SUPABASE_ANON_KEY")
        return 1

    client = create_client(supabase_url, supabase_key)

    logging.info("Fetching existing conversations...")
    existing_conversations = fetch_all_rows(
        client.table("conversations"),
        "id,user_id,title,mode",
        order_col="created_at",
    )

    conversation_map: Dict[Tuple[str, str, Optional[str]], str] = {}
    for row in existing_conversations:
        user_id = row.get("user_id")
        title = row.get("title")
        mode = row.get("mode")
        if user_id and title:
            conversation_map[build_conversation_key(user_id, title, mode)] = row.get("id")

    logging.info("Fetching history rows...")
    history_rows = fetch_all_rows(
        client.table("history"),
        "*",
        order_col="created_at",
    )

    if not history_rows:
        logging.info("No history rows found. Nothing to migrate.")
        return 0

    messages_batch: List[Dict[str, Any]] = []
    inserted_conversations = 0
    skipped_conversations = 0
    skipped_rows = 0
    prepared_messages = 0

    for row in history_rows:
        user_id = row.get("user_id")
        if not user_id:
            skipped_rows += 1
            logging.warning("Skipping row without user_id: %s", row.get("id"))
            continue

        topic = pick_first(
            row.get("topic"),
            row.get("prompt"),
            row.get("question"),
            row.get("title"),
        )
        if not topic:
            skipped_rows += 1
            logging.warning("Skipping row without topic/prompt for user %s", user_id)
            continue

        mode = row.get("mode")
        conversation_key = build_conversation_key(user_id, topic, mode)

        if conversation_key in conversation_map:
            skipped_conversations += 1
            continue

        if args.dry_run:
            conversation_id = str(uuid.uuid4())
            conversation_map[conversation_key] = conversation_id
            inserted_conversations += 1
        else:
            settings_payload = {}
            if row.get("levels") is not None:
                settings_payload["levels"] = row.get("levels")
            settings_payload["source"] = "history_v1"

            response = client.table("conversations").insert(
                {
                    "user_id": user_id,
                    "title": topic,
                    "mode": mode,
                    "settings": settings_payload,
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("created_at"),
                }
            ).execute()
            data = response.data or []
            if not data:
                logging.error("Failed to insert conversation for user %s topic %s", user_id, topic)
                skipped_rows += 1
                continue

            conversation_id = data[0]["id"]
            conversation_map[conversation_key] = conversation_id
            inserted_conversations += 1

        user_content = pick_first(
            row.get("prompt"),
            row.get("question"),
            row.get("topic"),
        )
        assistant_content = pick_first(
            row.get("response"),
            row.get("answer"),
            row.get("output"),
        )

        if not user_content or not assistant_content:
            logging.warning("Skipping messages for conversation %s due to missing content", conversation_id)
            skipped_rows += 1
            continue

        user_message = {
            "conversation_id": conversation_id,
            "role": "user",
            "content": user_content,
            "created_at": row.get("created_at"),
        }

        assistant_metadata = {
            "mode": mode,
            "levels": row.get("levels"),
            "models_used": row.get("models_used") or row.get("models"),
            "tokens": row.get("tokens"),
            "source_history_id": row.get("id"),
        }
        assistant_message = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": assistant_content,
            "metadata": {k: v for k, v in assistant_metadata.items() if v is not None},
            "created_at": row.get("created_at"),
        }

        messages_batch.extend([user_message, assistant_message])
        prepared_messages += 2

        if len(messages_batch) >= args.batch_size:
            if args.dry_run:
                logging.info("Dry run: prepared %d messages", len(messages_batch))
            else:
                try:
                    client.table("messages").insert(messages_batch).execute()
                    logging.info("Inserted %d messages", len(messages_batch))
                except Exception as exc:
                    logging.exception("Failed to insert messages batch: %s", exc)
            messages_batch = []

    if messages_batch:
        if args.dry_run:
            logging.info("Dry run: prepared %d messages", len(messages_batch))
        else:
            try:
                client.table("messages").insert(messages_batch).execute()
                logging.info("Inserted %d messages", len(messages_batch))
            except Exception as exc:
                logging.exception("Failed to insert messages batch: %s", exc)

    logging.info("Summary: conversations prepared=%d skipped_existing=%d", inserted_conversations, skipped_conversations)
    logging.info("Summary: messages prepared=%d skipped_rows=%d", prepared_messages, skipped_rows)
    if args.dry_run:
        logging.info("Dry run complete. Re-run with --dry-run=false to write.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
