"""On-disk registry for local-first collection/document metadata."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalCollectionRegistry:
    """Persist collection/document metadata for filesystem-backed local mode."""

    def __init__(self, base_path: str = "data/rag"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.base_path / "local_registry.json"
        self._lock_path = self.base_path / "local_registry.lock"

    def _load(self) -> dict[str, Any]:
        if not self._registry_path.exists():
            return {"collections": [], "documents": [], "jobs": []}
        with self._registry_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            return {"collections": [], "documents": [], "jobs": []}
        loaded.setdefault("collections", [])
        loaded.setdefault("documents", [])
        loaded.setdefault("jobs", [])
        return loaded

    def _save(self, payload: dict[str, Any]) -> None:
        with self._registry_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def get_or_create_collection(
        self,
        *,
        api_key_id: str,
        collection_id: str | None,
        collection_name: str | None,
    ) -> dict[str, Any]:
        with FileLock(str(self._lock_path)):
            payload = self._load()
            collections: list[dict[str, Any]] = payload["collections"]

            if collection_id:
                for item in collections:
                    if (
                        item.get("id") == collection_id
                        and item.get("api_key_id") == api_key_id
                        and item.get("deleted_at") is None
                    ):
                        return item
                raise KeyError("Collection not found")

            if not collection_name:
                raise ValueError("collection_id or collection_name is required")

            for item in collections:
                if (
                    item.get("api_key_id") == api_key_id
                    and item.get("name") == collection_name
                    and item.get("deleted_at") is None
                ):
                    return item

            created = {
                "id": str(uuid4()),
                "api_key_id": api_key_id,
                "name": collection_name,
                "description": None,
                "created_at": _now_iso(),
                "deleted_at": None,
            }
            collections.append(created)
            self._save(payload)
            return created

    def list_collections(self, api_key_id: str) -> list[dict[str, Any]]:
        with FileLock(str(self._lock_path)):
            payload = self._load()
            collections = [
                item
                for item in payload["collections"]
                if item.get("api_key_id") == api_key_id and item.get("deleted_at") is None
            ]
            collections.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return collections

    def create_document_and_job(
        self,
        *,
        api_key_id: str,
        collection_id: str,
        filename: str,
        source_url: str | None,
        content_hash: str,
        metadata: dict[str, Any],
        status: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with FileLock(str(self._lock_path)):
            payload = self._load()
            document = {
                "id": str(uuid4()),
                "api_key_id": api_key_id,
                "collection_id": collection_id,
                "filename": filename,
                "source_url": source_url,
                "content_hash": content_hash,
                "metadata": metadata,
                "created_at": _now_iso(),
            }
            job = {
                "id": str(uuid4()),
                "api_key_id": api_key_id,
                "document_id": document["id"],
                "status": status,
                "created_at": _now_iso(),
                "completed_at": _now_iso() if status == "completed" else None,
                "last_error": None,
            }
            payload["documents"].append(document)
            payload["jobs"].append(job)
            self._save(payload)
            return document, job

    def mark_collection_deleted(self, *, api_key_id: str, collection_id: str, base_path: str = "data/rag") -> bool:
        with FileLock(str(self._lock_path)):
            payload = self._load()
            found = False
            for item in payload["collections"]:
                if item.get("id") == collection_id and item.get("api_key_id") == api_key_id:
                    item["deleted_at"] = _now_iso()
                    found = True
                    break
            if found:
                self._save(payload)

        namespace_dir = Path(base_path) / api_key_id / collection_id
        if namespace_dir.exists():
            shutil.rmtree(namespace_dir)
            parent = namespace_dir.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        return found
