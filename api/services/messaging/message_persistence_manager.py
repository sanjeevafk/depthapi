"""Unified message persistence layer - replaces 3 duplicate nested functions."""

from datetime import datetime, timezone
from typing import Any, Optional

from api.logging_config import logger


class MessagePersistenceManager:
    """
    Consolidates all Supabase message writes into single service.
    
    Replaces:
    - _persist_user_message() (nested function)
    - _persist_assistant_message() (nested function)
    - _persist_conversation_update() (nested function)
    
    Benefits:
    - Single error handling strategy
    - Centralized logging
    - Testable DB layer
    - DRY principle
    """
    
    def __init__(self, supabase_client: Any):
        """Initialize with Supabase client."""
        self.supabase = supabase_client
    
    async def persist_user_message(
        self,
        conversation_id: str,
        content: str,
        client_id: str,
        sequence_id: Optional[int],
        mode: str,
        request_id: str,
        user_id_hash: str,
    ) -> None:
        """Insert user message into database.
        
        Args:
            conversation_id: Conversation ID
            content: Message content
            client_id: Client-generated message ID
            sequence_id: Sequence ID from Redis (if available)
            mode: Current mode
            request_id: Request ID for logging
            user_id_hash: Anonymized user ID for logging
        """
        if not self.supabase:
            return
        
        payload = {
            "id": client_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": content,
            "metadata": {"client_id": client_id, "mode": mode},
        }
        if sequence_id is not None:
            payload["sequence_id"] = sequence_id
        
        try:
            await self.supabase.table("messages").insert(payload).execute()
            logger.info(
                "messages_user_inserted",
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                sequence_id=sequence_id,
            )
        except Exception as exc:
            logger.error(
                "messages_user_insert_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                content_length=len(content),
                mode=mode,
                sampled=False,
            )
    
    async def persist_assistant_message(
        self,
        conversation_id: str,
        content: str,
        assistant_id: str,
        sequence_id: Optional[int],
        assistant_client_id: str,
        mode: str,
        request_id: str,
        user_id_hash: str,
    ) -> None:
        """Insert assistant message into database.
        
        Args:
            conversation_id: Conversation ID
            content: Message content
            assistant_id: Server-generated assistant message ID
            sequence_id: Sequence ID from Redis (if available)
            assistant_client_id: Client-provided assistant ID
            mode: Current mode
            request_id: Request ID for logging
            user_id_hash: Anonymized user ID for logging
        """
        if not self.supabase or not content.strip():
            return
        
        payload = {
            "id": assistant_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
            "metadata": {
                "mode": mode,
                "assistant_client_id": assistant_client_id,
            },
        }
        if sequence_id is not None:
            payload["sequence_id"] = sequence_id
        
        try:
            await self.supabase.table("messages").insert(payload).execute()
            logger.info(
                "messages_assistant_inserted",
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                sequence_id=sequence_id,
            )
        except Exception as exc:
            logger.error(
                "messages_assistant_insert_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                content_length=len(content),
                mode=mode,
                sampled=False,
            )
    
    async def update_conversation(
        self,
        conversation_id: str,
        mode: str,
        prompt_mode: str,
        request_id: str,
        user_id_hash: str,
    ) -> None:
        """Update conversation metadata.
        
        Args:
            conversation_id: Conversation ID
            mode: Current mode
            prompt_mode: Current prompt mode
            request_id: Request ID for logging
            user_id_hash: Anonymized user ID for logging
        """
        if not self.supabase:
            return
        
        payload = {
            "mode": mode,
            "settings": {"mode": mode, "prompt_mode": prompt_mode},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            await self.supabase.table("conversations").update(payload).eq(
                "id", conversation_id
            ).execute()
            logger.info(
                "messages_conversation_updated",
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                mode=mode,
            )
        except Exception as exc:
            logger.warning(
                "messages_conversation_update_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                sampled=False,
            )
