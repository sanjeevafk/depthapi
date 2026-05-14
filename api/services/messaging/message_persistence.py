"""Message persistence helpers for the messages route."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, cast

from api.repositories.chat_repository import ChatRepository
from api.logging_config import logger


async def insert_message_bundle(
    *,
    conversation_id: str,
    content: str,
    user_metadata: dict,
    assistant_metadata: dict,
    update_payload: dict,
    request_id: str,
    user_id_hash: str | None,
    retry: bool,
) -> str | None:
    assistant_message_id = await ChatRepository.insert_message_bundle_rpc(
        conversation_id,
        content,
        user_metadata,
        assistant_metadata,
        update_payload,
    )

    if assistant_message_id:
        return assistant_message_id

    _insert_user, _update_conv, _insert_assistant = ChatRepository.batch_insert_message_setup(
        conversation_id,
        content,
        user_metadata,
        assistant_metadata,
        update_payload,
    )

    user_res, conv_res, assistant_resp = await asyncio.gather(
        _insert_user(),
        _update_conv(),
        _insert_assistant(),
        return_exceptions=True,
    )

    for res, name in [(user_res, "user_insert"), (conv_res, "conv_update"), (assistant_resp, "assistant_insert")]:
        if isinstance(res, Exception):
            logger.error(
                f"messages_{name}_failed",
                error=str(res),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                retry=retry,
                sampled=False,
            )
            if name != "conv_update":
                raise res

    assistant_data = cast(list[Dict[str, Any]], assistant_resp.data) if not isinstance(assistant_resp, Exception) and getattr(assistant_resp, "data", None) else []
    return assistant_data[0]["id"] if assistant_data else None
