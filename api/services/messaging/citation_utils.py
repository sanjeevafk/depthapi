"""Shared helpers for building citation payloads from retrieved contexts."""

from __future__ import annotations

from typing import Any


def contexts_from_telemetry(telemetry_sink: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(telemetry_sink, dict):
        return []
    retrieved = telemetry_sink.get("retrieved_contexts")
    if not isinstance(retrieved, list):
        return []
    contexts: list[dict[str, Any]] = []
    for item in retrieved:
        if not isinstance(item, dict):
            continue
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        contexts.append({
            "doc_id": item.get("document_id"),
            "chunk_id": item.get("chunk_id") or item.get("id"),
            "text": item.get("content", ""),
            "score": item.get("score"),
            "vector_similarity": item.get("vector_similarity"),
            "rerank_score": item.get("rerank_score"),
            "token_count": item.get("token_count"),
            "match_source": item.get("match_source"),
            "source": citation.get("source_url") or citation.get("filename") or citation.get("source_tier"),
            "metadata": item.get("metadata") or {},
            "citation": citation,
        })
    return contexts


def citations_from_contexts(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    citations: list[dict[str, Any]] = []
    for ctx in contexts:
        chunk_id = str(ctx.get("chunk_id") or "")
        if chunk_id and chunk_id in seen:
            continue
        if chunk_id:
            seen.add(chunk_id)
        citations.append({
            "doc_id": ctx.get("doc_id"),
            "chunk_id": ctx.get("chunk_id"),
            "source": ctx.get("source"),
            "score": ctx.get("score"),
            "metadata": ctx.get("metadata") or {},
        })
    return citations


def stream_metadata_payload(telemetry_sink: dict[str, Any] | None) -> dict[str, Any]:
    contexts = contexts_from_telemetry(telemetry_sink)
    citations = citations_from_contexts(contexts)
    metadata: dict[str, Any] = {}
    if isinstance(telemetry_sink, dict):
        if isinstance(telemetry_sink.get("model_alias"), str):
            metadata["model_alias"] = telemetry_sink["model_alias"]
        if isinstance(telemetry_sink.get("token_usage"), dict):
            metadata["token_usage"] = telemetry_sink["token_usage"]
        if telemetry_sink.get("model_inference_ms") is not None:
            metadata["model_inference_ms"] = telemetry_sink["model_inference_ms"]
    return {
        "type": "metadata",
        "metadata": metadata,
        "citations": citations,
    }
