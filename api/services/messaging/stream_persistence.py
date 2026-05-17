"""Persistence helpers for streaming message flow."""

from datetime import datetime, timezone
from typing import Any

from api.logging_config import logger
from api.services.messaging.message_utils import safe_number


class StreamPersistence:
    """Persist user/assistant messages and conversation updates."""

    def __init__(
        self,
        *,
        supabase: Any,
        request_id: str,
        user_id_hash: str,
        conversation_id: str,
        client_message_id: str,
        assistant_message_id: str,
        assistant_client_id: str,
        selected_mode: str,
        prompt_mode: str,
        content: str,
        regenerate: bool,
        prompt_spec: dict[str, Any] | None = None,
    ) -> None:
        self.supabase = supabase
        self.request_id = request_id
        self.user_id_hash = user_id_hash
        self.conversation_id = conversation_id
        self.client_message_id = client_message_id
        self.assistant_message_id = assistant_message_id
        self.assistant_client_id = assistant_client_id
        self.selected_mode = selected_mode
        self.prompt_mode = prompt_mode
        self.prompt_spec = prompt_spec
        self.content = content
        self.regenerate = regenerate

        self.user_metadata = {
            "client_id": client_message_id,
            "mode": selected_mode,
            "prompt_mode": prompt_mode,
            "assistant_message_id": assistant_message_id,
        }
        self.assistant_metadata = {
            "assistant_client_id": assistant_client_id,
            "mode": selected_mode,
            "prompt_mode": prompt_mode,
        }

    async def persist_user_message(self, sequence_id: int | None) -> None:
        if not self.supabase:
            return
        payload = {
            "id": self.client_message_id,
            "conversation_id": self.conversation_id,
            "role": "user",
            "content": self.content,
            "metadata": self.user_metadata,
        }
        safe_sequence_id = safe_number(sequence_id, default=None)
        if safe_sequence_id is not None:
            payload["sequence_id"] = safe_sequence_id
        try:
            await self.supabase.table("messages").insert(payload).execute()
            logger.info(
                "messages_user_inserted",
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                sequence_id=safe_sequence_id,
            )
        except Exception as exc:
            logger.error(
                "messages_user_insert_failed",
                error=str(exc),
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                payload={
                    "role": "user",
                    "content_length": len(self.content),
                    "mode": self.selected_mode,
                    "sequence_id": safe_sequence_id,
                },
                retry=bool(self.regenerate),
                sampled=False,
            )

    async def persist_assistant_message(self, sequence_id: int | None, content_value: str) -> None:
        if not self.supabase:
            return
        payload = {
            "id": self.assistant_message_id,
            "conversation_id": self.conversation_id,
            "role": "assistant",
            "content": content_value,
            "metadata": self.assistant_metadata,
        }
        safe_sequence_id = safe_number(sequence_id, default=None)
        if safe_sequence_id is not None:
            payload["sequence_id"] = safe_sequence_id
        try:
            await self.supabase.table("messages").insert(payload).execute()
            logger.info(
                "messages_assistant_inserted",
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                sequence_id=safe_sequence_id,
            )
        except Exception as exc:
            logger.error(
                "messages_assistant_insert_failed",
                error=str(exc),
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                payload={
                    "role": "assistant",
                    "content_length": len(content_value),
                    "mode": self.selected_mode,
                    "sequence_id": safe_sequence_id,
                },
                retry=bool(self.regenerate),
                sampled=False,
            )

    async def persist_conversation_update(self) -> None:
        if not self.supabase:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        update_payload = {
            "mode": self.selected_mode,
            "settings": {"mode": self.selected_mode, "prompt_mode": self.prompt_mode},
            "updated_at": now_iso,
        }
        if self.prompt_spec:
            update_payload["prompt_spec"] = self.prompt_spec
        try:
            await (
                self.supabase.table("conversations")
                .update(update_payload)
                .eq("id", self.conversation_id)
                .execute()
            )
            logger.info(
                "messages_conversation_updated",
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                mode=self.selected_mode,
            )
        except Exception as exc:
            logger.warning(
                "messages_conversation_update_failed",
                error=str(exc),
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.conversation_id,
                payload=update_payload,
                retry=bool(self.regenerate),
                sampled=False,
            )
