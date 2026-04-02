import asyncio
from typing import Any, Optional
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import auth
from auth import verify_token, verify_token_optional
from config import get_settings
from logging_config import anonymize_user_id, logger
import services.share_manager as share_manager
from services.token_count import count_prompt_tokens

router = APIRouter(tags=["shares"])

class ShareCreateRequest(BaseModel):
    message_id: Optional[str] = Field(default=None)
    conversation_id: Optional[str] = Field(default=None)
    share_kind: Optional[str] = Field(default="response")
    access_level: Optional[str] = Field(default="public")
    title: Optional[str] = Field(default=None, max_length=140)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        trimmed = value.strip()
        return trimmed or None


class ShareCreateResponse(BaseModel):
    share_id: str
    share_token: str
    share_url: str
    access_level: str
    expires_at: Optional[str]


class ShareSnapshotResponse(BaseModel):
    id: str
    share_token: str
    title: Optional[str]
    prompt_text: str
    response_text: str
    metadata: dict[str, Any]
    access_level: str
    share_kind: str
    snapshot_messages: list[dict[str, Any]]
    created_at: str
    expires_at: Optional[str]
    view_count: int


class ShareListItem(BaseModel):
    id: str
    share_token: str
    share_url: str
    title: Optional[str]
    access_level: str
    created_at: str
    expires_at: Optional[str]
    view_count: int


class ShareListResponse(BaseModel):
    items: list[ShareListItem]
    page: int
    page_size: int


def _require_uuid(value: str, field_name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a UUID") from exc


def _parse_allowed_origins(raw_allowed_origins: str) -> list[str]:
    if not raw_allowed_origins.strip():
        return []
    parsed = [origin.strip() for origin in raw_allowed_origins.split(",") if origin.strip()]
    if "*" in parsed:
        logger.warning(
            "share_origin_wildcard_disallowed",
            configured_origins=raw_allowed_origins,
        )
        parsed = [origin for origin in parsed if origin != "*"]
    return parsed


def _origin_host_port(url: str) -> Optional[tuple[str, int]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (parsed.hostname.lower(), port)


def _resolve_base_url(request: Optional[Request]) -> str:
    settings = get_settings()
    configured = str(settings.public_base_url or "").rstrip("/")
    allowed_origins = _parse_allowed_origins(str(settings.allowed_origins or ""))
    allowed_host_ports = set()
    for origin in allowed_origins:
        host_port = _origin_host_port(origin)
        if host_port:
            allowed_host_ports.add(host_port)
    configured_host_port = _origin_host_port(configured)
    if configured_host_port:
        allowed_host_ports.add(configured_host_port)
    if request is not None:
        origin = (request.headers.get("origin") or "").strip()
        if origin:
            origin_host_port = _origin_host_port(origin)
            if origin_host_port and origin_host_port in allowed_host_ports:
                return origin.rstrip("/")
        request_base = str(request.base_url).rstrip("/")
        request_host_port = _origin_host_port(request_base)
        if request_host_port and request_host_port in allowed_host_ports:
            return request_base
    return configured


def _build_share_url(token: str, request: Optional[Request]) -> str:
    base = _resolve_base_url(request)
    return f"{base}/share/{token}"


def _normalize_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_record(data: Any) -> Optional[dict[str, Any]]:
    if isinstance(data, list) and data:
        record = data[0]
        return record if isinstance(record, dict) else None
    if isinstance(data, dict):
        return data
    return None


def _build_snapshot_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    total_tokens = 0
    for message in messages:
        content = str(message.get("content") or "")
        tokens = count_prompt_tokens(content)
        if tokens == 0 and not content:
            continue
        if total_tokens + tokens > max_tokens and snapshot:
            break
        total_tokens += tokens
        snapshot.append(
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "content": content,
                "created_at": message.get("created_at"),
            }
        )
    return snapshot


@router.post("/shares", response_model=ShareCreateResponse)
async def create_share(
    payload: ShareCreateRequest,
    request: Request,
    auth_data: dict = Depends(verify_token),
):
    user = auth_data["user"]
    user_id = str(user.id)

    supabase = auth.get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        access_level = share_manager.normalize_access_level(payload.access_level or "public")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        share_kind = share_manager.normalize_share_kind(payload.share_kind or "response")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    prompt_text = ""
    prompt_message_id: Optional[str] = None
    response_text = ""
    snapshot_messages: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    title: Optional[str] = payload.title

    if share_kind == "response":
        if not payload.message_id:
            raise HTTPException(status_code=400, detail="message_id is required for response shares")
        message_id = _require_uuid(payload.message_id, "message_id")

        message = None
        conversation = None
        prompt_message = None
        try:
            bundle_response = await asyncio.to_thread(
                lambda: supabase.rpc(
                    "fetch_share_response_bundle",
                    {"p_message_id": message_id, "p_owner_id": user_id},
                ).execute()
            )
            bundle = getattr(bundle_response, "data", None)
            if isinstance(bundle, list) and bundle:
                bundle = bundle[0]
            if isinstance(bundle, dict):
                message = _first_record(bundle.get("message"))
                conversation = _first_record(bundle.get("conversation"))
                prompt_message = _first_record(bundle.get("prompt"))
        except Exception as exc:
            logger.warning("share_bundle_rpc_failed", error=str(exc))

        if not message or not conversation:
            try:
                message_response = await asyncio.to_thread(
                    lambda: supabase.table("messages")
                    .select("id, conversation_id, role, content, metadata, created_at")
                    .eq("id", message_id)
                    .limit(1)
                    .execute()
                )
                message = _first_record(message_response.data)
                if not message:
                    raise HTTPException(status_code=404, detail="Message not found")
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("share_message_lookup_failed", error=str(exc))
                raise HTTPException(status_code=500, detail="Failed to load message") from exc

            if str(message.get("role") or "").lower() != "assistant":
                raise HTTPException(status_code=400, detail="Only assistant messages can be shared")

            conversation_id = str(message.get("conversation_id") or "")
            if not conversation_id:
                raise HTTPException(status_code=400, detail="Conversation not found")

            try:
                conversation_response = await asyncio.to_thread(
                    lambda: supabase.table("conversations")
                    .select("id, user_id, title, mode")
                    .eq("id", conversation_id)
                    .limit(1)
                    .execute()
                )
                conversation = _first_record(conversation_response.data)
                if not conversation:
                    raise HTTPException(status_code=404, detail="Conversation not found")
            except HTTPException:
                raise
            except Exception as exc:
                logger.error(
                    "share_conversation_lookup_failed",
                    error=str(exc),
                    user_id_hash=anonymize_user_id(user_id),
                )
                raise HTTPException(status_code=500, detail="Failed to load conversation") from exc

            if str(conversation.get("user_id") or "") != user_id:
                raise HTTPException(status_code=404, detail="Message not found")

            assistant_created_at = message.get("created_at")
            try:
                prompt_response = await asyncio.to_thread(
                    lambda: supabase.table("messages")
                    .select("id, content, created_at")
                    .eq("conversation_id", conversation_id)
                    .eq("role", "user")
                    .lte("created_at", assistant_created_at)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                prompt_message = _first_record(prompt_response.data)
            except Exception as exc:
                logger.warning("share_prompt_lookup_failed", error=str(exc))

        if str(message.get("role") or "").lower() != "assistant":
            raise HTTPException(status_code=400, detail="Only assistant messages can be shared")

        conversation_id = str(message.get("conversation_id") or "")
        if not conversation_id:
            raise HTTPException(status_code=400, detail="Conversation not found")

        if not prompt_message:
            prompt_message = {}

        prompt_text = str(prompt_message.get("content") or "")
        prompt_message_id = str(prompt_message.get("id") or "") or None
        assistant_created_at = message.get("created_at")

        raw_metadata = _normalize_metadata(message.get("metadata"))
        metadata = {
            "assistant_metadata": raw_metadata,
            "assistant_created_at": assistant_created_at,
            "conversation_title": conversation.get("title") if conversation else None,
            "conversation_mode": conversation.get("mode") if conversation else None,
            "prompt_message_id": prompt_message_id,
        }
        response_text = str(message.get("content") or "")

        if not title:
            fallback = str(conversation.get("title") or "").strip() if conversation else ""
            if fallback:
                title = fallback
            else:
                prompt_snippet = prompt_text.strip()
                if prompt_snippet:
                    title = prompt_snippet[:80]
    else:
        if not payload.conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id is required for conversation shares")
        conversation_id = _require_uuid(payload.conversation_id, "conversation_id")
        try:
            conversation_response = await asyncio.to_thread(
                lambda: supabase.table("conversations")
                .select("id, user_id, title, mode")
                .eq("id", conversation_id)
                .limit(1)
                .execute()
            )
            conversation = _first_record(conversation_response.data)
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "share_conversation_lookup_failed",
                error=str(exc),
                user_id_hash=anonymize_user_id(user_id),
            )
            raise HTTPException(status_code=500, detail="Failed to load conversation") from exc

        if str(conversation.get("user_id") or "") != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")

        try:
            messages_response = await asyncio.to_thread(
                lambda: supabase.table("messages")
                .select("id, role, content, created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=True)
                .limit(120)
                .execute()
            )
            raw_messages = [
                record
                for record in (messages_response.data or [])
                if isinstance(record, dict)
            ]
        except Exception as exc:
            logger.error("share_conversation_messages_failed", error=str(exc))
            raise HTTPException(status_code=500, detail="Failed to load conversation messages") from exc

        raw_messages.reverse()
        snapshot_messages = _build_snapshot_messages(raw_messages, max_tokens=12000)
        if not snapshot_messages:
            raise HTTPException(status_code=400, detail="Conversation has no messages to share")

        metadata = {
            "conversation_title": conversation.get("title"),
            "conversation_mode": conversation.get("mode"),
            "snapshot_message_count": len(snapshot_messages),
        }

        if not title:
            fallback = str(conversation.get("title") or "").strip()
            if fallback:
                title = fallback
            else:
                title = "Shared conversation snapshot"

    share_token = await asyncio.to_thread(share_manager.ensure_unique_token, supabase)

    share_payload = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "owner_id": user_id,
        "share_token": share_token,
        "access_level": access_level,
        "share_kind": share_kind,
        "title": title,
        "prompt_text": prompt_text,
        "response_text": response_text,
        "metadata": metadata,
        "password_hash": None,
        "expiry_days": None,
        "expires_at": None,
        "snapshot_messages": snapshot_messages,
    }

    try:
        share = await asyncio.to_thread(lambda: share_manager.create_share(supabase, share_payload))
    except Exception as exc:
        logger.error("share_create_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to create share") from exc

    return ShareCreateResponse(
        share_id=str(share.get("id")),
        share_token=share_token,
        share_url=_build_share_url(share_token, request),
        access_level=access_level,
        expires_at=share_payload.get("expires_at"),
    )


@router.get("/shares/{share_token}", response_model=ShareSnapshotResponse)
async def get_share(share_token: str, request: Request, auth_data: Optional[dict] = Depends(verify_token_optional)):
    supabase = auth.get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    token = share_token.strip()
    if not token:
        raise HTTPException(status_code=404, detail="Share not found")

    try:
        share = await asyncio.to_thread(lambda: share_manager.fetch_share_by_token(supabase, token))
    except Exception as exc:
        logger.error("share_fetch_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch share") from exc

    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share_manager.is_expired(share):
        raise HTTPException(status_code=410, detail="Share has expired")

    user_id = None
    if auth_data and isinstance(auth_data, dict):
        user = auth_data.get("user")
        user_id = str(getattr(user, "id", "") or "") or None

    access_granted = share_manager.verify_share_access(share, user_id, None)
    if not access_granted:
        raise HTTPException(status_code=403, detail="Access denied")

    current_view_count = int(share.get("view_count") or 0)
    await asyncio.to_thread(share_manager.increment_view_count, supabase, share)

    return ShareSnapshotResponse(
        id=str(share.get("id")),
        share_token=str(share.get("share_token")),
        title=share.get("title"),
        prompt_text=str(share.get("prompt_text") or ""),
        response_text=str(share.get("response_text") or ""),
        metadata=_normalize_metadata(share.get("metadata")),
        access_level=str(share.get("access_level") or ""),
        share_kind=str(share.get("share_kind") or "response"),
        snapshot_messages=list(share.get("snapshot_messages") or []),
        created_at=str(share.get("created_at")),
        expires_at=str(share.get("expires_at")) if share.get("expires_at") else None,
        view_count=current_view_count + 1,
    )


@router.post("/shares/{share_id}/revoke")
async def revoke_share(share_id: str, auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    user_id = str(user.id)

    supabase = auth.get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    share_uuid = _require_uuid(share_id, "share_id")

    try:
        share = await asyncio.to_thread(lambda: share_manager.fetch_share_by_id(supabase, share_uuid))
    except Exception as exc:
        logger.error("share_fetch_failed", error=str(exc), user_id_hash=anonymize_user_id(user_id))
        raise HTTPException(status_code=500, detail="Failed to fetch share") from exc

    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if str(share.get("owner_id") or "") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        await asyncio.to_thread(
            lambda: supabase.table("shared_responses")
            .delete()
            .eq("id", share_uuid)
            .eq("owner_id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error("share_revoke_failed", error=str(exc), user_id_hash=anonymize_user_id(user_id))
        raise HTTPException(status_code=500, detail="Failed to revoke share") from exc

    return {"status": "revoked"}


@router.get("/shares/list/user", response_model=ShareListResponse)
async def list_user_shares(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    auth_data: dict = Depends(verify_token),
):
    user = auth_data["user"]
    user_id = str(user.id)

    supabase = auth.get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    page = max(page, 1)
    page_size = min(max(page_size, 1), 50)
    start = (page - 1) * page_size
    end = start + page_size - 1

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("shared_responses")
            .select("id, share_token, title, access_level, created_at, expires_at, view_count")
            .eq("owner_id", user_id)
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )
        data = [
            record
            for record in (response.data or [])
            if isinstance(record, dict)
        ]
    except Exception as exc:
        logger.error("share_list_failed", error=str(exc), user_id_hash=anonymize_user_id(user_id))
        raise HTTPException(status_code=500, detail="Failed to fetch shares") from exc

    items = [
        ShareListItem(
            id=str(item.get("id")),
            share_token=str(item.get("share_token")),
            share_url=_build_share_url(str(item.get("share_token")), request),
            title=_string_or_none(item.get("title")),
            access_level=str(item.get("access_level") or ""),
            created_at=str(item.get("created_at")),
            expires_at=_string_or_none(item.get("expires_at")),
            view_count=_int_or_zero(item.get("view_count")),
        )
        for item in data
    ]

    return ShareListResponse(items=items, page=page, page_size=page_size)
