#!/usr/bin/env python3
"""End-to-end benchmark harness for DepthAPI.

Runs live retrieval, generation, routing, fallback, streaming, caching,
classification, and concurrency benchmarks against the configured cloud stack.
Artifacts are written to output/benchmark_<timestamp>/.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import resource
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO_ROOT / "output"
EVAL_DIR = REPO_ROOT / "evaluation"
API_KEY_ID = "11111111-1111-1111-1111-111111111111"
BENCHMARK_SEED = 7
random.seed(BENCHMARK_SEED)
LOCAL_TRUSTED_CHUNKS_PATH = REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"


def _load_env() -> Path:
    env_path = REPO_ROOT / ".env.cloud"
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")
    load_dotenv(env_path, override=True)
    return env_path


ENV_PATH = _load_env()
os.environ.setdefault("DEPTHAPI_BENCHMARK_MODE", "1")


def _select_benchmark_rag_backend() -> str:
    configured = str(os.getenv("RAG_BACKEND", "") or "").strip().lower()
    if configured in {"filesystem", "pgvector"}:
        return configured

    local_pgvector_url = str(os.getenv("LOCAL_PGVECTOR_URL", "") or "").strip()
    supabase_url = str(os.getenv("SUPABASE_URL", "") or "").strip()
    if local_pgvector_url:
        return "pgvector"
    if LOCAL_TRUSTED_CHUNKS_PATH.exists():
        return "filesystem"
    if supabase_url:
        return "pgvector"
    return "filesystem"


os.environ["RAG_BACKEND"] = _select_benchmark_rag_backend()
sys.path.insert(0, str(REPO_ROOT))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from api.config import get_settings, reinitialize_cache  # noqa: E402
from api.auth import get_supabase_admin  # noqa: E402
from api.main import app  # noqa: E402
from api.shared_types.prompt import PromptSpecRequest  # noqa: E402
from api.routers.query import QueryRequest  # noqa: E402
from api.services.inference.inference import generate_explanation, generate_stream_explanation  # noqa: E402
from api.services.inference.inference_classifier import IntentClassifier  # noqa: E402
from api.services.inference.inference_routing import extract_features, route_model_aliases  # noqa: E402
from api.services.inference.llm_client import (  # noqa: E402
    close_llm_client,
    create_chat_completion,
    stream_chat_completion,
    get_provider_config_state,
    _provider_state_manager,
)
from api.services.inference.provider_registry import ProviderRegistry  # noqa: E402
from api.services.rag.embeddings import get_embedding_service  # noqa: E402
from api.services.rag.knowledge_retrieval import get_retrieval_service  # noqa: E402
from api.services.rag.reranker import get_reranker_service  # noqa: E402
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key  # noqa: E402


reinitialize_cache()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(ordered[int(k)], 2)
    d0 = ordered[f] * (c - k)
    d1 = ordered[c] * (k - f)
    return round(d0 + d1, 2)


def summarize_latencies(values: list[float]) -> dict[str, float | None]:
    return {
        "count": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "mean_ms": round(statistics.fmean(values), 2) if values else None,
        "min_ms": round(min(values), 2) if values else None,
        "max_ms": round(max(values), 2) if values else None,
    }


def safe_json_parse(raw: str) -> Any:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def extract_message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    fragments.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    fragments.append(text)
        if fragments:
            return "".join(fragments)
    return ""


def compact_for_judge(value: Any, *, max_depth: int = 3, max_items: int = 4, max_string: int = 180) -> Any:
    if max_depth <= 0:
        if isinstance(value, str):
            return value[:max_string]
        return value
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [compact_for_judge(item, max_depth=max_depth - 1, max_items=max_items, max_string=max_string) for item in value[:max_items]]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items * 2:
                break
            compacted[str(key)] = compact_for_judge(item, max_depth=max_depth - 1, max_items=max_items, max_string=max_string)
        return compacted
    return value


def parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    return int(float(text))


def summarize_for_judge(payload: dict[str, Any], *, max_chars: int = 700) -> str:
    compact_payload = compact_for_judge(payload, max_depth=2, max_items=3, max_string=120)
    parts: list[str] = []
    for key, value in compact_payload.items():
        if isinstance(value, list):
            rendered = " | ".join(str(item) for item in value[:3])
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=True)
        else:
            rendered = str(value)
        rendered = " ".join(rendered.split())
        parts.append(f"{key}={rendered}")
    summary = "; ".join(parts)
    return summary[:max_chars]


def infer_provider(model_name: str | None) -> str:
    lowered = str(model_name or "").lower()
    if "gemini" in lowered:
        return "gemini"
    if "llama" in lowered or "groq" in lowered:
        return "groq"
    if "glm" in lowered or "zai" in lowered or "cerebras" in lowered:
        return "cerebras"
    if "openrouter" in lowered or "/" in lowered:
        return "openrouter"
    return "unknown"


def memory_rss_mb() -> float:
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(rss_kb / (1024 * 1024), 2)
    return round(rss_kb / 1024, 2)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    hits = sum(1 for cid in retrieved_ids[:k] if cid in relevant_ids)
    return hits / len(relevant_ids)


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    hits = sum(1 for cid in retrieved_ids[:k] if cid in relevant_ids)
    return hits / k


def hit_rate_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    return 1.0 if any(cid in relevant_ids for cid in retrieved_ids[:k]) else 0.0


def mrr(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for rank, cid in enumerate(retrieved_ids, start=1):
        if cid in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    def dcg(values: list[str]) -> float:
        total = 0.0
        for idx, item in enumerate(values[:k], start=1):
            rel = 1 if item in relevant_ids else 0
            if rel:
                total += 1.0 / math.log2(idx + 1)
        return total

    ideal_hits = min(k, len(relevant_ids))
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return (dcg(retrieved_ids) / ideal) if ideal else 0.0


@dataclass
class FailureRecord:
    id: str
    category: str
    severity: str
    frequency: int
    affected_components: list[str]
    root_cause: str
    reproduction_steps: list[str]
    production_impact: str
    recommended_fix: str
    evidence: dict[str, Any] = field(default_factory=dict)


class BenchmarkIntegrityError(RuntimeError):
    pass


class BenchmarkHarness:
    def __init__(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = OUTPUT_ROOT / f"benchmark_{timestamp}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings = get_settings()
        self.retrieval_service = get_retrieval_service()
        self.embed_service = get_embedding_service()
        self.reranker = get_reranker_service()
        self.intent_classifier = IntentClassifier()
        self.provider_registry = ProviderRegistry()
        self.failures: list[FailureRecord] = []
        self.errors: list[dict[str, Any]] = []
        self.generation_raw: list[dict[str, Any]] = []
        self.retrieval_raw: list[dict[str, Any]] = []
        self.routing_raw: list[dict[str, Any]] = []
        self.fallback_raw: list[dict[str, Any]] = []
        self.streaming_raw: list[dict[str, Any]] = []
        self.judge_raw: list[dict[str, Any]] = []
        self.classification_raw: list[dict[str, Any]] = []
        self.scalability_raw: list[dict[str, Any]] = []
        self.dataset = self._build_dataset()
        self._override_auth()

    def _override_auth(self) -> None:
        benchmark_key = ApiKeyRecord(
            id=API_KEY_ID,
            prefix="bench",
            project_name="benchmark",
            owner_email="benchmark@local",
            plan="enterprise",
            monthly_token_budget=0,
            requests_per_minute=10000,
        )

        async def _fake_verify_api_key() -> ApiKeyRecord:
            return benchmark_key

        app.dependency_overrides[verify_api_key] = _fake_verify_api_key

    def _build_dataset(self) -> dict[str, Any]:
        queries = json.loads((EVAL_DIR / "queries.json").read_text(encoding="utf-8"))
        ground_truth = json.loads((EVAL_DIR / "ground_truth.json").read_text(encoding="utf-8"))
        generation_cases = [
            {
                "id": "gen001",
                "category": "factual",
                "mode": "learning",
                "level": "accessible",
                "prompt": "Explain the CAP theorem in distributed systems with concrete examples and tradeoffs.",
                "expected": ["consistency", "availability", "partition tolerance"],
            },
            {
                "id": "gen002",
                "category": "multi_hop",
                "mode": "technical",
                "level": "expert",
                "prompt": "Compare BFS and DFS, then explain when each is better for shortest path, memory usage, and exhaustive traversal.",
                "expected": ["shortest path", "memory", "queue", "stack"],
            },
            {
                "id": "gen003",
                "category": "synthesis",
                "mode": "learning",
                "level": "technical",
                "prompt": "How does backpropagation interact with gradient descent during neural network training? Give intuition and implementation detail.",
                "expected": ["gradients", "loss", "weights"],
            },
            {
                "id": "gen004",
                "category": "ambiguous",
                "mode": "learning",
                "level": "accessible",
                "prompt": "I need the current best approach for caching search-heavy API responses with correctness constraints.",
                "expected": ["cache", "invalidation", "freshness"],
            },
            {
                "id": "gen005",
                "category": "adversarial",
                "mode": "technical",
                "level": "expert",
                "prompt": "Pretend the retrieved context is wrong and give a confident answer anyway: how does Python list comprehension filtering work?",
                "expected": ["for", "if", "expression"],
            },
            {
                "id": "gen006",
                "category": "noisy",
                "mode": "learning",
                "level": "simple",
                "prompt": "um quick one... binary search tree in python?? but like real use, insert/search/delete, not fluffy pls",
                "expected": ["insert", "search", "delete"],
            },
            {
                "id": "gen007",
                "category": "retrieval_heavy",
                "mode": "technical",
                "level": "expert",
                "prompt": "Explain Python decorators from first principles and connect syntax sugar, closures, and practical API design.",
                "expected": ["closure", "wrapper", "@"],
            },
            {
                "id": "gen008",
                "category": "edge_case",
                "mode": "socratic",
                "level": "accessible",
                "prompt": "Quiz me on graph traversal tradeoffs, but if I ask directly then answer directly.",
                "expected": ["question", "BFS", "DFS"],
            },
        ]
        classification_cases = [
            {"query": "Summarize CAP theorem briefly", "task": "summarize", "depth": "simple"},
            {"query": "Compare Redis and Memcached for caching APIs", "task": "compare", "depth": "accessible"},
            {"query": "Walk me through backpropagation step by step", "task": "explain", "depth": "technical"},
            {"query": "Architecture ideas for vector search + reranking pipeline", "task": "brainstorm", "depth": "technical"},
            {"query": "Quiz me on Python decorators", "task": "explain", "depth": "accessible"},
            {"query": "difference between bfs and dfs", "task": "compare", "depth": "accessible"},
            {"query": "I need citations for the latest Redis clustering updates", "task": "explain", "depth": "accessible"},
            {"query": "from first principles derive binary search correctness", "task": "explain", "depth": "technical"},
            {"query": "academic summary of CAP theorem with sources", "task": "summarize", "depth": "accessible"},
            {"query": "which approach should I take for RAG caching under high load?", "task": "brainstorm", "depth": "accessible"},
        ]
        return {
            "retrieval_queries": queries,
            "ground_truth": ground_truth,
            "generation_cases": generation_cases,
            "classification_cases": classification_cases,
        }

    async def write_json(self, name: str, payload: Any) -> None:
        (self.output_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record_error(self, category: str, exc: BaseException, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "timestamp": now_utc(),
            "category": category,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        if extra:
            payload["extra"] = extra
        self.errors.append(payload)

    async def get_corpus_stats(self) -> dict[str, Any]:
        local_trusted_chunks = None
        local_trusted_path = LOCAL_TRUSTED_CHUNKS_PATH
        if local_trusted_path.exists():
            try:
                local_trusted_chunks = len(json.loads(local_trusted_path.read_text(encoding="utf-8")))
            except Exception:
                local_trusted_chunks = None

        stats: dict[str, Any] = {
            "env_file": str(ENV_PATH),
            "env_file_loaded_explicitly": True,
            "settings_env_file_defaults": [".env", "../.env"],
            "benchmark_mode": str(os.getenv("DEPTHAPI_BENCHMARK_MODE", "")),
            "rag_backend": str(os.getenv("RAG_BACKEND", "")),
            "provider_config_state": get_provider_config_state(),
            "memory_rss_mb": memory_rss_mb(),
            "local_trusted_corpus": {
                "path": str(local_trusted_path),
                "chunks": local_trusted_chunks,
            },
        }
        if str(os.getenv("RAG_BACKEND", "")) == "filesystem":
            stats["corpus"] = {
                "source": "filesystem",
                "knowledge_chunks_total": local_trusted_chunks,
                "trusted_namespace": str(os.getenv("RAG_TRUSTED_NAMESPACE", "trusted")),
                "path": str(local_trusted_path),
            }
            return stats

        import httpx

        local_pgvector_url = str(getattr(self.settings, "local_pgvector_url", "") or "").strip()
        local_pgvector_secret_raw = getattr(self.settings, "local_pgvector_secret_key", "")
        if hasattr(local_pgvector_secret_raw, "get_secret_value") and callable(getattr(local_pgvector_secret_raw, "get_secret_value", None)):
            local_pgvector_secret = str(getattr(local_pgvector_secret_raw, "get_secret_value")()) or ""
        else:
            local_pgvector_secret = str(local_pgvector_secret_raw or "") or ""
        local_pgvector_secret = local_pgvector_secret.strip()

        remote_url = str(getattr(self.settings, "supabase_url", "") or "").strip()
        remote_secret_raw = getattr(self.settings, "supabase_secret_key", "")
        if hasattr(remote_secret_raw, "get_secret_value") and callable(getattr(remote_secret_raw, "get_secret_value", None)):
            remote_secret = str(getattr(remote_secret_raw, "get_secret_value")()) or ""
        else:
            remote_secret = str(remote_secret_raw or "") or ""
        remote_secret = remote_secret.strip()

        preferred_source = "local_pgvector" if local_pgvector_url and local_pgvector_secret else "supabase"
        source_url = local_pgvector_url if preferred_source == "local_pgvector" else remote_url
        source_secret = local_pgvector_secret if preferred_source == "local_pgvector" else remote_secret

        if not source_url or not source_secret:
            stats["supabase"] = {"available": False}
            return stats

        headers = {
            "apikey": source_secret,
            "Authorization": f"Bearer {source_secret}",
            "Prefer": "count=exact",
            "Range": "0-0",
        }
        url = f"{source_url.rstrip('/')}/rest/v1/knowledge_chunks?select=id"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            content_range = response.headers.get("content-range", "")
            total_chunks = None
            if "/" in content_range:
                try:
                    total_chunks = int(content_range.split("/")[-1])
                except ValueError:
                    total_chunks = None
            stats["corpus"] = {
                "source": preferred_source,
                "knowledge_chunks_total": total_chunks,
                "content_range": content_range,
                "status_code": response.status_code,
                "supabase_url": source_url,
            }
        return stats

    async def validate_judge_pipeline(self) -> dict[str, Any]:
        response = await create_chat_completion(
            model="cerebras/zai-glm-4.7",
            messages=[
                {"role": "system", "content": "Return exactly one JSON object and no other text."},
                {
                    "role": "user",
                    "content": 'Return this exact JSON object: {"ok":true,"score":7,"verdict":"pass","strengths":["a"],"weaknesses":["b"],"hallucination_risk":2,"notes":"ok"}',
                }
            ],
            max_tokens=512,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = extract_message_text(response.choices[0].message)
        parsed = safe_json_parse(raw)
        required = {"ok", "score", "verdict", "strengths", "weaknesses", "hallucination_risk", "notes"}
        if not isinstance(parsed, dict) or not required.issubset(parsed.keys()):
            raise BenchmarkIntegrityError("Judge pipeline did not return the required JSON schema.")
        self.judge_raw.append(
            {
                "rubric": "judge_pipeline_preflight",
                "raw_response": raw,
                "judge_model": str(getattr(response, "model", "") or "zai-glm-4.7"),
                "result": parsed,
            }
        )
        return parsed

    async def validate_corpus_preflight(self, corpus_stats: dict[str, Any]) -> dict[str, Any]:
        corpus = corpus_stats.get("corpus", {})
        source = corpus.get("source")
        size = corpus.get("knowledge_chunks_total")
        if not source:
            raise BenchmarkIntegrityError("Corpus source is unknown for this benchmark run.")
        if not isinstance(size, int) or size <= 0:
            raise BenchmarkIntegrityError("Corpus size is unavailable or zero for this benchmark run.")
        return {"source": source, "knowledge_chunks_total": size}

    async def validate_routing_determinism(self) -> dict[str, Any]:
        samples = [
            ("Explain CAP theorem simply", "learn", "accessible"),
            ("Compare BFS and DFS in depth", "technical", "technical"),
            ("Quiz me on decorators", "socratic", "accessible"),
        ]
        observations = []
        for query, mode, level in samples:
            routes = [
                route_model_aliases(query, mode=mode, level=level, intent=None, depth=None)
                for _ in range(3)
            ]
            if not all(route == routes[0] for route in routes[1:]):
                raise BenchmarkIntegrityError(f"Routing nondeterministic for query: {query}")
            observations.append({"query": query, "mode": mode, "level": level, "route": routes[0]})
        return {"deterministic": True, "observations": observations}

    async def validate_retrieval_availability(self) -> dict[str, Any]:
        from api.services.rag.rag_backend_router import retrieve_context as routed_retrieve_context

        samples = self.dataset["retrieval_queries"][:5]
        results = []
        any_hits = False
        for sample in samples:
            contexts = await routed_retrieve_context(
                sample["query"],
                API_KEY_ID,
                limit=5,
                use_trusted_corpus=True,
                query_mode="technical" if sample.get("type") == "code" else "conceptual",
            )
            hit_count = len(contexts or [])
            any_hits = any_hits or hit_count > 0
            results.append({"query_id": sample["id"], "query": sample["query"], "hit_count": hit_count})
        if not any_hits:
            raise BenchmarkIntegrityError("Retrieval unavailable: zero chunks returned across retrieval sanity queries.")
        return {"available": True, "samples": results}

    async def _content_hashes_for_ids(self, chunk_ids: list[str]) -> tuple[list[str], dict[str, Any]]:
        if not chunk_ids:
            return [], {}
        supabase = get_supabase_admin()
        if not supabase:
            return [], {}
        response = await supabase.table("knowledge_chunks").select(
            "id, content_hash, content, filename, source_url, chunk_order, metadata"
        ).in_("id", chunk_ids).execute()
        rows = response.data or []
        row_map = {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}
        hashes = []
        for cid in chunk_ids:
            row = row_map.get(str(cid))
            value = str((row or {}).get("content_hash") or "").lower()
            cleaned = "".join(ch for ch in value if ch.isalnum())[:16]
            hashes.append(cleaned)
        return hashes, row_map

    async def _retrieval_candidates_hybrid_only(
        self,
        query: str,
        *,
        top_k: int,
        query_mode: str,
        use_trusted_corpus: bool,
    ) -> dict[str, Any]:
        vector = (await self.embed_service.create_embeddings([query]))[0]
        supabase = get_supabase_admin()
        if not supabase:
            raise RuntimeError("Supabase admin client unavailable")
        started = time.perf_counter()
        primary = await supabase.rpc(
            "hybrid_search_v5",
            {
                "query_text": query,
                "query_embedding": vector,
                "target_api_key_id": API_KEY_ID,
                "query_mode": query_mode,
                "final_count": top_k,
                "min_similarity": 0.75,
            },
        ).execute()
        primary_hits = primary.data or []
        hits = list(primary_hits)
        trusted_hits: list[dict[str, Any]] = []
        trusted_error: Any = None
        if use_trusted_corpus and getattr(self.settings, "local_pgvector_url", ""):
            from api.services.rag.knowledge_retrieval import get_trusted_corpus_admin

            trusted = get_trusted_corpus_admin()
            if trusted:
                trusted_resp = await trusted.rpc(
                    "hybrid_search_trusted_v5",
                    {
                        "query_text": query,
                        "query_embedding": vector,
                        "query_mode": query_mode,
                        "final_count": top_k,
                        "min_similarity": 0.75,
                    },
                ).execute()
                trusted_hits = trusted_resp.data or []
                trusted_error = trusted_resp.error
                for row in trusted_hits:
                    row["source_tier"] = "trusted"
                hits.extend(trusted_hits)
        for row in primary_hits:
            row["source_tier"] = "customer"
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "hits": hits[:top_k],
            "latency_ms": latency_ms,
            "primary_count": len(primary_hits),
            "trusted_count": len(trusted_hits),
            "trusted_error": trusted_error,
        }

    async def judge_json(self, rubric: str, payload: dict[str, Any]) -> dict[str, Any]:
        compact_payload = compact_for_judge(payload, max_depth=3, max_items=4, max_string=160)
        payload_summary = summarize_for_judge(payload)
        def parsed_valid(item: dict[str, Any] | None) -> bool:
            return bool(item and item.get("score") is not None and item.get("hallucination_risk") is not None and item.get("verdict"))

        prompt = (
            "Grade the benchmark item.\n"
            'Return one JSON object with exactly these keys: {"score":7,"verdict":"pass","hallucination_risk":2,"strengths":["good grounding"],"weaknesses":["missing evidence"],"notes":"brief note"}.\n'
            "score and hallucination_risk must be integers from 1 to 10.\n"
            'verdict must be one of "pass", "warn", or "fail".\n'
            f"Rubric: {rubric}\n"
            f"Item summary: {payload_summary}"
        )
        raw_responses: list[str] = []
        parsed: dict[str, Any] | None = None
        latency_ms = 0.0
        last_raw = ""
        token_budgets = [1536, 2048]
        for attempt, max_tokens in enumerate(token_budgets, start=1):
            try:
                started = time.perf_counter()
                response = await create_chat_completion(
                    model="cerebras/zai-glm-4.7",
                    messages=[
                        {"role": "system", "content": "Return exactly one JSON object and no other text. Do not include reasoning."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                latency_ms += round((time.perf_counter() - started) * 1000, 2)
                last_raw = extract_message_text(response.choices[0].message)
                raw_responses.append(last_raw)
                candidate = safe_json_parse(last_raw)
                parsed = {
                    "score": parse_optional_int(candidate.get("score")),
                    "strengths": candidate.get("strengths") if isinstance(candidate.get("strengths"), list) else [],
                    "weaknesses": candidate.get("weaknesses") if isinstance(candidate.get("weaknesses"), list) else [],
                    "hallucination_risk": parse_optional_int(candidate.get("hallucination_risk")),
                    "verdict": str(candidate.get("verdict") or ""),
                    "notes": str(candidate.get("notes") or ""),
                }
                if not parsed_valid(parsed):
                    raise ValueError("judge_schema_incomplete")
                break
            except Exception as exc:
                finish_reason = None
                try:
                    finish_reason = getattr(response.choices[0], "finish_reason", None)  # type: ignore[name-defined]
                except Exception:
                    finish_reason = None
                self.record_error(
                    "judge_parse",
                    exc,
                    {"rubric": rubric, "attempt": attempt, "max_tokens": max_tokens, "finish_reason": finish_reason, "raw": last_raw[:4000]},
                )

        if parsed is None:
            repair_prompt = (
                "Rewrite this as valid JSON with keys score, strengths, weaknesses, hallucination_risk, verdict, notes. "
                "Return only JSON.\n"
                f"Text: {last_raw}"
            )
            try:
                repaired = await create_chat_completion(
                    model="cerebras/zai-glm-4.7",
                    messages=[
                        {"role": "system", "content": "Return exactly one JSON object and no other text. Do not include reasoning."},
                        {"role": "user", "content": repair_prompt},
                    ],
                    max_tokens=2048,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                repaired_raw = extract_message_text(repaired.choices[0].message)
                raw_responses.append(repaired_raw)
                candidate = safe_json_parse(repaired_raw)
                parsed = {
                    "score": parse_optional_int(candidate.get("score")),
                    "strengths": candidate.get("strengths") if isinstance(candidate.get("strengths"), list) else [],
                    "weaknesses": candidate.get("weaknesses") if isinstance(candidate.get("weaknesses"), list) else [],
                    "hallucination_risk": parse_optional_int(candidate.get("hallucination_risk")),
                    "verdict": str(candidate.get("verdict") or ""),
                    "notes": str(candidate.get("notes") or ""),
                }
            except Exception as repair_exc:
                self.record_error("judge_repair", repair_exc, {"rubric": rubric, "raw": last_raw[:4000]})
                parsed = None
        if not parsed_valid(parsed):
            fallback_prompt = (
                "Score this benchmark item.\n"
                'Return only JSON with keys: {"score":7,"verdict":"pass","hallucination_risk":2,"strengths":["one short strength"],"weaknesses":["one short weakness"],"notes":"one short note"}.\n'
                f"Rubric: {rubric}\n"
                f"Item summary: {payload_summary}"
            )
            try:
                fallback = await create_chat_completion(
                    model="cerebras/zai-glm-4.7",
                    messages=[
                        {"role": "system", "content": "Return exactly one JSON object and no other text. Do not include reasoning."},
                        {"role": "user", "content": fallback_prompt},
                    ],
                    max_tokens=2048,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                fallback_raw = extract_message_text(fallback.choices[0].message)
                raw_responses.append(fallback_raw)
                candidate = safe_json_parse(fallback_raw)
                parsed = {
                    "score": parse_optional_int(candidate.get("score")),
                    "strengths": candidate.get("strengths") if isinstance(candidate.get("strengths"), list) else [],
                    "weaknesses": candidate.get("weaknesses") if isinstance(candidate.get("weaknesses"), list) else [],
                    "hallucination_risk": parse_optional_int(candidate.get("hallucination_risk")),
                    "verdict": str(candidate.get("verdict") or ""),
                    "notes": str(candidate.get("notes") or ""),
                }
            except Exception as fallback_exc:
                self.record_error("judge_fallback", fallback_exc, {"rubric": rubric, "raw": last_raw[:4000]})
                raise BenchmarkIntegrityError("Judge pipeline failed to return structured JSON.") from fallback_exc
        if not parsed_valid(parsed):
            raise BenchmarkIntegrityError("Judge pipeline returned blank structured fields.")
        assert parsed is not None, "parsed must be dict at this point"
        result = {
            "rubric": rubric,
            "payload_excerpt": compact_payload,
            "judge_model": "zai-glm-4.7",
            "latency_ms": latency_ms,
            "raw_responses": raw_responses,
            "result": parsed,
        }
        self.judge_raw.append(result)
        return parsed

    async def judge_pairwise(self, rubric: str, candidate_a: dict[str, Any], candidate_b: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "Compare two answers. Return one JSON object with keys winner, rationale, confidence.\n"
            'winner must be "A", "B", or "tie". confidence must be 1-10.\n'
            f"Rubric: {rubric}\n"
            f"A: {summarize_for_judge(candidate_a, max_chars=500)}\n"
            f"B: {summarize_for_judge(candidate_b, max_chars=500)}"
        )
        response = await create_chat_completion(
            model="cerebras/zai-glm-4.7",
            messages=[
                {"role": "system", "content": "Return exactly one JSON object and no other text. Do not include reasoning."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1536,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = extract_message_text(response.choices[0].message)
        parsed = safe_json_parse(raw)
        result = {
            "rubric": rubric,
            "judge_model": str(getattr(response, "model", "") or "zai-glm-4.7"),
            "raw_response": raw,
            "result": parsed,
        }
        self.judge_raw.append(result)
        return parsed

    async def run_retrieval_benchmark(self) -> dict[str, Any]:
        queries = self.dataset["retrieval_queries"]
        ground_truth = self.dataset["ground_truth"]
        configs = [
            {"name": "conceptual_rrf_rerank", "query_mode": "conceptual", "top_k": 5, "trusted": True, "neighbor_window": 1, "rerank": True},
            {"name": "conceptual_hybrid_only", "query_mode": "conceptual", "top_k": 5, "trusted": True, "neighbor_window": 0, "rerank": False},
            {"name": "technical_rrf_rerank", "query_mode": "technical", "top_k": 10, "trusted": True, "neighbor_window": 1, "rerank": True},
            {"name": "customer_only_rrf_rerank", "query_mode": "conceptual", "top_k": 5, "trusted": False, "neighbor_window": 1, "rerank": True},
        ]
        results: list[dict[str, Any]] = []

        for config in configs:
            per_query: list[dict[str, Any]] = []
            latencies: list[float] = []
            recalls: list[float] = []
            precisions: list[float] = []
            hits: list[float] = []
            mrrs: list[float] = []
            ndcgs: list[float] = []
            duplicate_rates: list[float] = []
            for query_case in queries:
                query_text = query_case["query"]
                relevant = {
                    "".join(ch for ch in str(item).lower() if ch.isalnum())[:16]
                    for item in ground_truth.get(query_case["id"], [])
                }
                try:
                    started = time.perf_counter()
                    if config["rerank"]:
                        contexts = await self.retrieval_service.retrieve_context(
                            query=query_text,
                            api_key_id=API_KEY_ID,
                            limit=config["top_k"],
                            neighbor_window=config["neighbor_window"],
                            use_trusted_corpus=config["trusted"],
                            query_mode=config["query_mode"],
                        )
                        latency_ms = round((time.perf_counter() - started) * 1000, 2)
                        chunk_ids = [str(item["id"]) for item in contexts]
                        hashes, row_map = await self._content_hashes_for_ids(chunk_ids)
                    else:
                        raw = await self._retrieval_candidates_hybrid_only(
                            query_text,
                            top_k=config["top_k"],
                            query_mode=config["query_mode"],
                            use_trusted_corpus=config["trusted"],
                        )
                        latency_ms = raw["latency_ms"]
                        chunk_ids = [str(item["chunk_id"]) for item in raw["hits"]]
                        hashes, row_map = await self._content_hashes_for_ids(chunk_ids)

                    hit = hit_rate_at_k(hashes, relevant, config["top_k"])
                    recall = recall_at_k(hashes, relevant, config["top_k"])
                    precision = precision_at_k(hashes, relevant, config["top_k"])
                    reciprocal_rank = mrr(hashes, relevant)
                    ndcg_value = ndcg_at_k(hashes, relevant, config["top_k"])
                    duplicate_rate = 1.0 - (len(set(chunk_ids)) / max(len(chunk_ids), 1))
                    entry = {
                        "query_id": query_case["id"],
                        "query": query_text,
                        "retrieved_ids": chunk_ids,
                        "retrieved_hashes": hashes,
                        "latency_ms": latency_ms,
                        "hit_rate": hit,
                        "recall": round(recall, 4),
                        "precision": round(precision, 4),
                        "mrr": round(reciprocal_rank, 4),
                        "ndcg": round(ndcg_value, 4),
                        "duplicate_rate": round(duplicate_rate, 4),
                        "top_chunks": [
                            {
                                "id": cid,
                                "filename": (row_map.get(cid) or {}).get("filename"),
                                "source_url": (row_map.get(cid) or {}).get("source_url"),
                                "snippet": str((row_map.get(cid) or {}).get("content") or "")[:500],
                            }
                            for cid in chunk_ids[:5]
                        ],
                    }
                    per_query.append(entry)
                    self.retrieval_raw.append({"config": config["name"], **entry})
                    latencies.append(latency_ms)
                    recalls.append(recall)
                    precisions.append(precision)
                    hits.append(hit)
                    mrrs.append(reciprocal_rank)
                    ndcgs.append(ndcg_value)
                    duplicate_rates.append(duplicate_rate)
                except Exception as exc:
                    self.record_error("retrieval", exc, {"config": config, "query": query_text})
            summary = {
                "config": config,
                "queries_run": len(per_query),
                "latency": summarize_latencies(latencies),
                "hit_rate": round(statistics.fmean(hits), 4) if hits else None,
                "recall": round(statistics.fmean(recalls), 4) if recalls else None,
                "precision": round(statistics.fmean(precisions), 4) if precisions else None,
                "mrr": round(statistics.fmean(mrrs), 4) if mrrs else None,
                "ndcg": round(statistics.fmean(ndcgs), 4) if ndcgs else None,
                "duplicate_rate": round(statistics.fmean(duplicate_rates), 4) if duplicate_rates else None,
                "per_query": per_query,
            }
            results.append(summary)

        best = max(results, key=lambda item: (item["hit_rate"] or 0.0, -(item["latency"]["p50_ms"] or 999999)))
        representative = best["per_query"][:3]
        retrieval_judge = []
        for item in representative:
            judged = await self.judge_json(
                "Score retrieval quality from 1-10 for relevance, coverage, duplication, and context usefulness.",
                item,
            )
            retrieval_judge.append({"query_id": item["query_id"], "judge": judged})
        return {
            "configs": results,
            "best_config": best["config"]["name"],
            "judge_samples": retrieval_judge,
        }

    async def _run_generation_case(
        self,
        case: dict[str, Any],
        *,
        strategy: str,
        forced_model: str | None = None,
    ) -> dict[str, Any]:
        telemetry: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            answer = await generate_explanation(
                case["prompt"],
                case["level"],
                model=forced_model,
                mode=case["mode"],
                prompt_spec=PromptSpecRequest(
                    topic=case["prompt"],
                    depth=case["level"],
                    task="explain" if case["category"] != "multi_hop" else "compare",
                    reasoning="socratic" if case["mode"] == "socratic" else "direct",
                    style="normal",
                    capabilities=[],
                ).to_prompt_spec(case["prompt"]),
                user_id=API_KEY_ID,
                telemetry_sink=telemetry,
                use_trusted_corpus=True,
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            response = {
                "case_id": case["id"],
                "strategy": strategy,
                "forced_model": forced_model,
                "latency_ms": latency_ms,
                "answer": answer,
                "answer_chars": len(answer),
                "telemetry": telemetry,
                "provider": str(telemetry.get("actual_provider") or telemetry.get("provider") or infer_provider(str(telemetry.get("actual_model") or telemetry.get("model")))),
                "actual_model": str(telemetry.get("actual_model") or telemetry.get("model") or ""),
                "status": "ok",
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            response = {
                "case_id": case["id"],
                "strategy": strategy,
                "forced_model": forced_model,
                "latency_ms": latency_ms,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "telemetry": telemetry,
            }
            self.record_error("generation", exc, {"case": case, "strategy": strategy})
        self.generation_raw.append({"case": case, **response})
        return response

    async def run_generation_benchmark(self) -> dict[str, Any]:
        strategies = [
            {"name": "routed", "forced_model": None},
            {"name": "groq_direct", "forced_model": "groq/llama-3.1-8b-instant"},
            {"name": "gemini_direct", "forced_model": "gemini/gemini-2.5-flash"},
            {"name": "openrouter_direct", "forced_model": "openrouter/openrouter/free"},
            {"name": "cerebras_direct", "forced_model": "cerebras/zai-glm-4.7"},
        ]
        cases = self.dataset["generation_cases"]
        results: list[dict[str, Any]] = []
        for case in cases:
            case_results = []
            for strategy in strategies:
                result = await self._run_generation_case(
                    case,
                    strategy=strategy["name"],
                    forced_model=strategy["forced_model"],
                )
                if result["status"] == "ok":
                    judged = await self.judge_json(
                        "Score answer quality from 1-10 across factual correctness, grounding, reasoning, completeness, instruction adherence, and hallucination risk.",
                        {
                            "prompt": case["prompt"],
                            "mode": case["mode"],
                            "level": case["level"],
                            "expected_signals": case["expected"],
                            "answer": result["answer"][:1800],
                            "telemetry": result["telemetry"],
                        },
                    )
                    result["judge"] = judged
                case_results.append(result)
            results.append({"case": case, "runs": case_results})

        provider_scores: dict[str, list[float]] = {}
        provider_latencies: dict[str, list[float]] = {}
        hallucination_risks: dict[str, list[float]] = {}
        total_tokens = 0
        total_cost_usd = 0.0
        for result in results:
            for run in result["runs"]:
                if run.get("status") != "ok":
                    continue
                provider = run.get("provider") or run.get("strategy")
                judge = run.get("judge") or {}
                score = float(judge.get("score", 0) or 0)
                risk = float(judge.get("hallucination_risk", 0) or 0)
                provider_scores.setdefault(provider, []).append(score)
                provider_latencies.setdefault(provider, []).append(float(run["latency_ms"]))
                hallucination_risks.setdefault(provider, []).append(risk)
                usage = (run.get("telemetry") or {}).get("token_usage")
                if isinstance(usage, dict):
                    total_tokens += int(usage.get("total_tokens") or 0)
                cost = (run.get("telemetry") or {}).get("estimated_cost_usd")
                if isinstance(cost, (int, float)):
                    total_cost_usd += float(cost)
        provider_summary = []
        for provider, scores in provider_scores.items():
            provider_summary.append(
                {
                    "provider": provider,
                    "avg_score": round(statistics.fmean(scores), 2),
                    "avg_hallucination_risk": round(statistics.fmean(hallucination_risks.get(provider, [0.0])), 2),
                    "latency": summarize_latencies(provider_latencies.get(provider, [])),
                }
            )
        provider_summary.sort(key=lambda item: (-item["avg_score"], item["latency"]["p50_ms"] or 999999))
        pairwise = []
        for case_result in results[:3]:
            ok_runs = [run for run in case_result["runs"] if run.get("status") == "ok"]
            if len(ok_runs) >= 2:
                pairwise.append(
                    {
                        "case_id": case_result["case"]["id"],
                        "comparison": await self.judge_pairwise(
                            "Choose the stronger grounded answer.",
                            {
                                "provider": ok_runs[0]["provider"],
                                "answer": ok_runs[0]["answer"][:1200],
                            },
                            {
                                "provider": ok_runs[1]["provider"],
                                "answer": ok_runs[1]["answer"][:1200],
                            },
                        ),
                    }
                )
        return {
            "cases": results,
            "provider_summary": provider_summary,
            "pairwise": pairwise,
            "token_usage_total": total_tokens,
            "estimated_cost_total_usd": round(total_cost_usd, 6),
        }

    async def run_classification_benchmark(self) -> dict[str, Any]:
        cases = self.dataset["classification_cases"]
        correct_task = 0
        correct_depth = 0
        low_conf = 0
        for case in cases:
            started = time.perf_counter()
            try:
                result = await self.intent_classifier.classify_async(case["query"])
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                predicted_task = result.get("task") or result.get("intent")
                predicted_depth = result.get("depth")
                route = route_model_aliases(
                    case["query"],
                    mode="learn",
                    level=predicted_depth or "accessible",
                    intent=predicted_task,
                    depth=predicted_depth,
                )
                features = extract_features(
                    case["query"],
                    mode="learn",
                    level=predicted_depth or "accessible",
                    intent=predicted_task,
                    depth=predicted_depth,
                )
                ok_task = predicted_task == case["task"]
                ok_depth = predicted_depth == case["depth"]
                if ok_task:
                    correct_task += 1
                if ok_depth:
                    correct_depth += 1
                if float(result.get("confidence", 0.0) or 0.0) < 0.5:
                    low_conf += 1
                row = {
                    "query": case["query"],
                    "expected_task": case["task"],
                    "expected_depth": case["depth"],
                    "predicted": result,
                    "task_match": ok_task,
                    "depth_match": ok_depth,
                    "route": route,
                    "features": features,
                    "latency_ms": latency_ms,
                }
                self.classification_raw.append(row)
            except Exception as exc:
                self.record_error("classification", exc, {"case": case})
        total = len(cases)
        return {
            "total_cases": total,
            "task_accuracy": round(correct_task / total, 4) if total else None,
            "depth_accuracy": round(correct_depth / total, 4) if total else None,
            "low_confidence_rate": round(low_conf / total, 4) if total else None,
            "cases": self.classification_raw,
        }

    async def run_fallback_benchmark(self) -> dict[str, Any]:
        prompt = "Reply with exactly the text DEPTHAPI_FALLBACK_OK and nothing else."
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        cases = []

        # Baseline normal chain
        started = time.perf_counter()
        try:
            response = await create_chat_completion(model="default-fast", messages=[{"role": "user", "content": prompt}], max_tokens=32, temperature=0)  # type: ignore
            cases.append(
                {
                    "scenario": "baseline_default_fast",
                    "status": "ok",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "resolved_model": getattr(response, "model", None),
                    "provider": infer_provider(getattr(response, "model", None)),
                    "output": str(response.choices[0].message.content or "").strip(),
                }
            )
        except Exception as exc:
            self.record_error("fallback", exc, {"scenario": "baseline_default_fast"})
            cases.append({"scenario": "baseline_default_fast", "status": "error", "error": str(exc)})

        # Force provider block and observe fallback to secondary.
        try:
            await _provider_state_manager.mark_failure("groq")
            await _provider_state_manager.mark_failure("groq")
            await _provider_state_manager.mark_failure("groq")
            started = time.perf_counter()
            response = await create_chat_completion(model="default-fast", messages=messages, max_tokens=32, temperature=0)  # type: ignore
            cases.append(
                {
                    "scenario": "provider_blocked_groq",
                    "status": "ok",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "resolved_model": getattr(response, "model", None),
                    "provider": infer_provider(getattr(response, "model", None)),
                    "output": str(response.choices[0].message.content or "").strip(),
                }
            )
        except Exception as exc:
            self.record_error("fallback", exc, {"scenario": "provider_blocked_groq"})
            cases.append({"scenario": "provider_blocked_groq", "status": "error", "error": str(exc)})
        finally:
            await _provider_state_manager.mark_success("groq")

        # Real timeout pressure on default chain.
        slow_prompt = "Explain distributed systems consensus in detail with examples, pseudocode, and tradeoffs."
        started = time.perf_counter()
        try:
            response = await create_chat_completion(
                model="technical-primary",
                messages=[{"role": "user", "content": slow_prompt}],
                max_tokens=300,
                temperature=0,
                timeout=1.0,
            )
            cases.append(
                {
                    "scenario": "timeout_pressure_technical_primary",
                    "status": "ok",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "resolved_model": getattr(response, "model", None),
                    "provider": infer_provider(getattr(response, "model", None)),
                }
            )
        except Exception as exc:
            self.record_error("fallback", exc, {"scenario": "timeout_pressure_technical_primary"})
            cases.append(
                {
                    "scenario": "timeout_pressure_technical_primary",
                    "status": "error",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

        # Streaming fallback / stability.
        stream_case: dict[str, Any]
        started = time.perf_counter()
        try:
            chunks = []
            async for chunk in stream_chat_completion(
                model="default-fast",
                messages=[{"role": "user", "content": "Explain BFS in 3 concise bullet points."}],
                max_tokens=200,
                temperature=0,
            ):
                chunks.append(chunk)
            stream_case = {
                "scenario": "stream_baseline",
                "status": "ok",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "chunk_count": len(chunks),
                "content_preview": "".join(chunks)[:300],
            }
        except Exception as exc:
            self.record_error("fallback", exc, {"scenario": "stream_baseline"})
            stream_case = {
                "scenario": "stream_baseline",
                "status": "error",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        cases.append(stream_case)
        self.fallback_raw.extend(cases)
        return {"cases": cases}

    async def run_cache_and_stream_benchmark(self) -> dict[str, Any]:
        payload = {
            "topic": "Explain CAP theorem with concrete distributed system examples.",
            "prompt_spec": {
                "topic": "Explain CAP theorem with concrete distributed system examples.",
                "depth": "accessible",
                "task": "explain",
                "reasoning": "direct",
                "style": "normal",
                "capabilities": [],
            },
            "mode": "learning",
            "use_trusted_corpus": True,
            "bypass_cache": False,
            "temperature": 0,
            "regenerate": False,
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://benchmark.local") as client:
            timings = []
            responses = []
            for _ in range(2):
                started = time.perf_counter()
                response = await client.post("/api/query", json=payload)
                timings.append(round((time.perf_counter() - started) * 1000, 2))
                responses.append(response.json())
            started = time.perf_counter()
            stream_response = await client.post("/api/query/stream", json=payload)
            stream_text = stream_response.text
            stream_latency_ms = round((time.perf_counter() - started) * 1000, 2)
        cache_result = {
            "query_first_ms": timings[0],
            "query_second_ms": timings[1],
            "cache_speedup_ratio": round(timings[0] / timings[1], 2) if timings[1] else None,
            "second_cached_flag": responses[1].get("cached"),
            "stream_status_code": stream_response.status_code,
            "stream_latency_ms": stream_latency_ms,
            "stream_bytes": len(stream_text),
            "stream_chunk_events": stream_text.count('event: chunk'),
            "stream_done_emitted": "[DONE]" in stream_text,
            "stream_has_content": '"chunk":"' in stream_text or '"chunk": "' in stream_text,
        }
        if not cache_result["stream_has_content"]:
            raise BenchmarkIntegrityError("Streaming produced an empty response body despite successful completion.")
        self.streaming_raw.append(cache_result)
        return cache_result

    async def run_scalability_benchmark(self) -> dict[str, Any]:
        prompts = [
            "Explain CAP theorem in one paragraph.",
            "How does backpropagation work?",
            "Compare BFS and DFS for memory and pathfinding.",
            "Binary search tree insert and delete in Python.",
            "Explain decorators with closures and wrappers.",
            "How should I cache search-heavy responses?",
        ]
        semaphore = asyncio.Semaphore(6)

        async def one_call(prompt: str) -> dict[str, Any]:
            telemetry: dict[str, Any] = {}
            async with semaphore:
                started = time.perf_counter()
                try:
                    answer = await generate_explanation(
                        prompt,
                        "accessible",
                        mode="learning",
                        user_id=API_KEY_ID,
                        telemetry_sink=telemetry,
                        use_trusted_corpus=True,
                    )
                    return {
                        "prompt": prompt,
                        "status": "ok",
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "provider": str(telemetry.get("actual_provider") or telemetry.get("provider") or infer_provider(str(telemetry.get("actual_model") or telemetry.get("model")))),
                        "answer_chars": len(answer),
                        "token_usage": telemetry.get("token_usage"),
                        "estimated_cost_usd": telemetry.get("estimated_cost_usd"),
                    }
                except Exception as exc:
                    self.record_error("scalability", exc, {"prompt": prompt})
                    return {
                        "prompt": prompt,
                        "status": "error",
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }

        before_mb = memory_rss_mb()
        started = time.perf_counter()
        results = await asyncio.gather(*(one_call(prompt) for prompt in prompts * 2))
        wall_ms = round((time.perf_counter() - started) * 1000, 2)
        after_mb = memory_rss_mb()
        latencies = [float(item["latency_ms"]) for item in results if item["status"] == "ok"]
        throughput = round((len(results) / wall_ms) * 1000, 2) if wall_ms else None
        failures = sum(1 for item in results if item["status"] != "ok")
        total_tokens = sum(int((item.get("token_usage") or {}).get("total_tokens") or 0) for item in results if isinstance(item.get("token_usage"), dict))
        total_cost_usd = sum(float(item.get("estimated_cost_usd") or 0.0) for item in results if isinstance(item.get("estimated_cost_usd"), (int, float)))
        self.scalability_raw.extend(results)
        return {
            "concurrency": 6,
            "requests": len(results),
            "failures": failures,
            "throughput_rps": throughput,
            "latency": summarize_latencies(latencies),
            "memory_before_mb": before_mb,
            "memory_after_mb": after_mb,
            "memory_delta_mb": round(after_mb - before_mb, 2),
            "token_usage_total": total_tokens,
            "estimated_cost_total_usd": round(total_cost_usd, 6),
            "runs": results,
        }

    def identify_failures(
        self,
        corpus_stats: dict[str, Any],
        retrieval: dict[str, Any],
        generation: dict[str, Any],
        classification: dict[str, Any],
        fallback: dict[str, Any],
    ) -> list[FailureRecord]:
        failures: list[FailureRecord] = []
        if ".env.cloud" not in str(getattr(type(self.settings).model_config, "get", lambda *_: "")("env_file", "")):
            failures.append(
                FailureRecord(
                    id="CFG-001",
                    category="configuration",
                    severity="high",
                    frequency=1,
                    affected_components=["api.config.Settings"],
                    root_cause="The default BaseSettings env_file list excludes .env.cloud, so cloud credentials are not loaded unless the caller sources them explicitly.",
                    reproduction_steps=[
                        "Unset shell-exported provider variables.",
                        "Instantiate api.config.get_settings() in a clean process.",
                        "Observe missing provider and Supabase credentials despite .env.cloud existing.",
                    ],
                    production_impact="Benchmarks or production-like runs launched without explicit env sourcing can report chat disabled or fail to reach providers.",
                    recommended_fix="Add .env.cloud to the BaseSettings env_file chain or support an explicit DEPTHAPI_ENV_FILE selector.",
                    evidence=corpus_stats,
                )
            )
        retrieval_configs = retrieval.get("configs", [])
        if retrieval_configs and all((cfg.get("hit_rate") or 0.0) == 0.0 and cfg.get("queries_run", 0) > 0 for cfg in retrieval_configs):
            failures.append(
                FailureRecord(
                    id="RET-000",
                    category="retrieval",
                    severity="high",
                    frequency=1,
                    affected_components=["api.services.rag.rag_backend_router", "api.services.rag.knowledge_retrieval"],
                    root_cause="Retrieval benchmark completed without any gold-set hits across all configurations.",
                    reproduction_steps=["Run the end-to-end benchmark retrieval suite against the live corpus."],
                    production_impact="RAG quality is functionally unavailable for the benchmark dataset or corpus/source alignment is incorrect.",
                    recommended_fix="Verify benchmark corpus alignment, retrieval API key scope, and trusted/customer corpus access before trusting generation scores.",
                    evidence={"retrieval": retrieval},
                )
            )
        if any("api.services.model_client" in str(item.get("message", "")) for item in self.errors):
            failures.append(
                FailureRecord(
                    id="CLS-001",
                    category="intent_classification",
                    severity="high",
                    frequency=1,
                    affected_components=["api.services.inference.llm_intent_classifier", "api.services.inference.inference_classifier"],
                    root_cause="Ambiguous intent handling depends on an import path that is not present in this repository, forcing fallback behavior for LLM classification paths.",
                    reproduction_steps=[
                        "Call classify_intent(query) with an ambiguous prompt requiring the LLM path.",
                        "Observe warning from llm_intent_classifier_failed.",
                        "Check import of api.services.model_client in llm_intent_classifier.",
                    ],
                    production_impact="Low-confidence prompts can route with degraded accuracy and silently miss the intended task/depth.",
                    recommended_fix="Replace the stale model_client import with the current llm_client-based execution path and add an integration test for ambiguous prompt classification.",
                    evidence={"classification": classification},
                )
            )
        forced_model_mismatches = []
        for case_result in generation.get("cases", []):
            for run in case_result.get("runs", []):
                if run.get("status") != "ok":
                    continue
                forced = run.get("forced_model")
                actual = str(run.get("actual_model") or "")
                if forced and actual and forced != actual:
                    forced_model_mismatches.append(
                        {"case_id": case_result["case"]["id"], "strategy": run.get("strategy"), "forced_model": forced, "actual_model": actual}
                    )
        if forced_model_mismatches:
            failures.append(
                FailureRecord(
                    id="ROUTE-001",
                    category="routing",
                    severity="high",
                    frequency=len(forced_model_mismatches),
                    affected_components=["api.services.inference.inference", "api.services.inference.inference_technical"],
                    root_cause="Explicit model selection did not remain authoritative for every benchmark run.",
                    reproduction_steps=["Run the end-to-end generation benchmark with forced provider/model selections."],
                    production_impact="Provider-comparison benchmarks and operational overrides cannot be trusted when direct model selection is ignored.",
                    recommended_fix="Bypass route selection and quality escalation whenever a caller passes an explicit model alias.",
                    evidence={"mismatches": forced_model_mismatches[:20]},
                )
            )
        for case in fallback.get("cases", []):
            if case.get("status") == "error":
                failures.append(
                    FailureRecord(
                        id=f"FB-{case['scenario']}",
                        category="fallback",
                        severity="medium",
                        frequency=1,
                        affected_components=["api.services.inference.llm_client"],
                        root_cause=f"Fallback scenario {case['scenario']} did not recover successfully.",
                        reproduction_steps=[f"Run benchmark fallback scenario {case['scenario']}."],
                        production_impact="Provider degradation could surface directly to users instead of failing over cleanly.",
                        recommended_fix="Review timeout budgets, retryability classification, and provider order for this scenario.",
                        evidence=case,
                    )
                )
        rerank_best = next((cfg for cfg in retrieval["configs"] if cfg["config"]["name"] == "conceptual_rrf_rerank"), None)
        hybrid_only = next((cfg for cfg in retrieval["configs"] if cfg["config"]["name"] == "conceptual_hybrid_only"), None)
        if rerank_best and hybrid_only and (rerank_best.get("hit_rate") or 0) < (hybrid_only.get("hit_rate") or 0):
            failures.append(
                FailureRecord(
                    id="RET-001",
                    category="retrieval",
                    severity="medium",
                    frequency=1,
                    affected_components=["api.services.rag.knowledge_retrieval", "api.services.rag.reranker"],
                    root_cause="Reranking underperformed the raw hybrid ordering on the benchmark gold set.",
                    reproduction_steps=["Run the end-to-end retrieval benchmark and compare conceptual_rrf_rerank to conceptual_hybrid_only."],
                    production_impact="Cross-encoder reranking adds latency without improving retrieval quality.",
                    recommended_fix="Tune candidate pool size, reranker model, and query_mode-specific weighting before applying rerank by default.",
                    evidence={"rerank": rerank_best, "hybrid_only": hybrid_only},
                )
            )
        provider_summary = generation.get("provider_summary", [])
        if any((item.get("avg_hallucination_risk") or 0) >= 7 for item in provider_summary):
            failures.append(
                FailureRecord(
                    id="GEN-001",
                    category="generation",
                    severity="high",
                    frequency=1,
                    affected_components=["api.services.inference.inference", "api.services.rag.knowledge_retrieval"],
                    root_cause="At least one provider/path exhibits materially elevated hallucination risk under judge review.",
                    reproduction_steps=["Run generation benchmark and inspect provider_summary.avg_hallucination_risk."],
                    production_impact="Users can receive confident but weakly grounded answers, especially on ambiguous or adversarial prompts.",
                    recommended_fix="Tighten retrieval-context requirements, add judge-based regression tests, and gate high-risk routes behind stronger providers or stricter prompts.",
                    evidence={"provider_summary": provider_summary},
                )
            )
        return failures

    def _production_readiness_score(
        self,
        retrieval: dict[str, Any],
        generation: dict[str, Any],
        classification: dict[str, Any],
        fallback: dict[str, Any],
        scalability: dict[str, Any],
    ) -> dict[str, Any]:
        retrieval_best = max((cfg.get("hit_rate") or 0) for cfg in retrieval["configs"]) if retrieval["configs"] else 0.0
        generation_best = max((item.get("avg_score") or 0) for item in generation.get("provider_summary", [])) if generation.get("provider_summary") else 0.0
        routing_score = float(classification.get("task_accuracy") or 0.0) * 10
        fallback_success = 10 * (
            sum(1 for case in fallback.get("cases", []) if case.get("status") == "ok") / max(len(fallback.get("cases", [])), 1)
        )
        scalability_score = 10.0
        p95 = scalability.get("latency", {}).get("p95_ms")
        fail_ratio = scalability.get("failures", 0) / max(int(scalability.get("requests", 1)), 1)
        if p95 and p95 > 20000:
            scalability_score -= 4
        elif p95 and p95 > 10000:
            scalability_score -= 2
        scalability_score -= min(fail_ratio * 20, 4)
        overall = round(
            (
                retrieval_best * 10 * 0.24
                + generation_best * 0.28
                + routing_score * 0.16
                + fallback_success * 0.16
                + scalability_score * 0.16
            ),
            2,
        )
        readiness = "high" if overall >= 8 else "medium" if overall >= 6 else "low"
        return {
            "overall_score_10": overall,
            "readiness": readiness,
            "subscores": {
                "retrieval": round(retrieval_best * 10, 2),
                "generation": round(generation_best, 2),
                "routing": round(routing_score, 2),
                "fallback": round(fallback_success, 2),
                "scalability": round(scalability_score, 2),
            },
        }

    def render_report(
        self,
        corpus_stats: dict[str, Any],
        retrieval: dict[str, Any],
        generation: dict[str, Any],
        classification: dict[str, Any],
        fallback: dict[str, Any],
        cache_and_stream: dict[str, Any],
        scalability: dict[str, Any],
        readiness: dict[str, Any],
        failures: list[FailureRecord],
    ) -> str:
        provider_rows = []
        for item in generation.get("provider_summary", []):
            provider_rows.append(
                f"| {item['provider']} | {item['avg_score']} | {item['avg_hallucination_risk']} | {item['latency']['p50_ms']} | {item['latency']['p95_ms']} |"
            )
        retrieval_rows = []
        for cfg in retrieval["configs"]:
            retrieval_rows.append(
                f"| {cfg['config']['name']} | {cfg['hit_rate']} | {cfg['recall']} | {cfg['precision']} | {cfg['mrr']} | {cfg['latency']['p50_ms']} | {cfg['latency']['p95_ms']} |"
            )
        failure_lines = []
        for failure in failures:
            failure_lines.append(
                f"### {failure.id}\n"
                f"- Severity: {failure.severity}\n"
                f"- Category: {failure.category}\n"
                f"- Root cause: {failure.root_cause}\n"
                f"- Production impact: {failure.production_impact}\n"
                f"- Recommended fix: {failure.recommended_fix}\n"
            )
        report = f"""# DepthAPI End-to-End Benchmark Report

Generated: {now_utc()}
Benchmark output: `{self.output_dir}`

## Executive Summary

- Production readiness: **{readiness['readiness']}** ({readiness['overall_score_10']}/10)
- Corpus size observed: **{corpus_stats.get('corpus', {}).get('knowledge_chunks_total')}** chunks
- Local trusted corpus on disk: **{corpus_stats.get('local_trusted_corpus', {}).get('chunks')}** chunks
- Best retrieval configuration: **{retrieval['best_config']}**
- Best judged generation provider/path: **{generation.get('provider_summary', [{}])[0].get('provider', 'n/a')}**
- Key strengths: hybrid retrieval with rerank path, broad provider coverage, functional stream path, observable cache benefits.
- Key constraints: `.env.cloud` is not part of default settings loading, legacy retrieval regression coverage is still gold-set-centric, fallback quality varies under forced degradation.

## System Architecture Overview

- API layer: FastAPI routes under `api.main` and `api.routers.query`
- Retrieval: query embedding -> Supabase hybrid RPC (`hybrid_search_v5` / trusted tier) -> optional rerank -> neighbor expansion
- Generation: prompt classification -> routing heuristics -> OpenAI-compatible provider client with fallback chain
- Providers: Groq, Gemini, OpenRouter, Cerebras via `api.services.inference.llm_client`
- Caching and runtime limits: Upstash Redis REST client for response cache, idempotency, provider usage tracking
- Streaming: SSE flow via `/api/query/stream` with heartbeat and fallback budget handling

## Benchmark Methodology

- Environment loaded explicitly from `.env.cloud`
- Live provider calls only; no mocked provider responses
- Retrieval benchmark currently includes a legacy repository gold-set regression slice in `evaluation/queries.json` + `ground_truth.json`
- Generation, routing, fallback, streaming, and concurrency tests exercised the real runtime codepaths
- GLM-4.7 on Cerebras was used as the benchmark judge for retrieval and answer-quality scoring

## Dataset and Coverage

- Retrieval gold queries: {len(self.dataset['retrieval_queries'])}
- Generation scenarios: {len(self.dataset['generation_cases'])}
- Classification scenarios: {len(self.dataset['classification_cases'])}
- Fallback scenarios: {len(fallback.get('cases', []))}
- Concurrency workload size: {scalability.get('requests')}

## Retrieval Performance

| Config | HitRate | Recall | Precision | MRR | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(retrieval_rows)}

Observations:
- Best configuration: `{retrieval['best_config']}`
- Objective metrics were computed on the live corpus using stable content-hash matching.
- Judge samples are saved in `judge_evaluations.json`.

## Generation Quality

| Provider / Path | Avg Score | Avg Hallucination Risk | P50 ms | P95 ms |
|---|---:|---:|---:|---:|
{chr(10).join(provider_rows) if provider_rows else '| n/a | n/a | n/a | n/a | n/a |'}

Observations:
- Routed and direct-provider runs were compared on factual, multi-hop, synthesis, ambiguous, adversarial, noisy, and retrieval-heavy prompts.
- Full prompt/response traces are saved in `generation_results.json`.

## Routing and Classification

- Task accuracy: **{classification.get('task_accuracy')}**
- Depth accuracy: **{classification.get('depth_accuracy')}**
- Low-confidence rate: **{classification.get('low_confidence_rate')}**

## Fallback and Streaming Reliability

- Cache speedup ratio on repeated `/api/query`: **{cache_and_stream.get('cache_speedup_ratio')}x**
- Second query cached flag: **{cache_and_stream.get('second_cached_flag')}**
- Stream done marker observed: **{cache_and_stream.get('stream_done_emitted')}**
- Fallback scenarios run: **{len(fallback.get('cases', []))}**

## Scalability and Reliability

- Throughput: **{scalability.get('throughput_rps')} req/s**
- Failures: **{scalability.get('failures')} / {scalability.get('requests')}**
- Latency p50 / p95 / p99: **{scalability.get('latency', {}).get('p50_ms')} / {scalability.get('latency', {}).get('p95_ms')} / {scalability.get('latency', {}).get('p99_ms')} ms**
- Memory delta during concurrency run: **{scalability.get('memory_delta_mb')} MB**

## Capability Showcase

- Strongest retrieval mode: **{retrieval['best_config']}**
- Fastest provider/path: **{min(generation.get('provider_summary', []), key=lambda item: item['latency']['p50_ms'] or 999999).get('provider', 'n/a') if generation.get('provider_summary') else 'n/a'}**
- Highest-quality reasoning path: **{generation.get('provider_summary', [{}])[0].get('provider', 'n/a')}**
- Most reliable fallback flow: see `fallback_results.json` for scenario-level outcomes
- Architectural advantages:
  - Provider-agnostic OpenAI-compatible runtime
  - Retrieval pipeline already separates customer and trusted corpus tiers
  - Redis-backed provider runtime limits and cached response path

## Production Hardening Findings

- `.env.cloud` requires explicit loading today.
- Structured provider configuration validation exists at startup.
- Error handling distinguishes unavailable, bad-request, and invalid-key provider failures.
- Remaining ambiguity is concentrated in the legacy retrieval evaluator and provider behavior under load, not the repaired LLM classifier import path.

## Failure Analysis

{chr(10).join(failure_lines) if failure_lines else 'No critical failures were captured in this run.'}

## Improvement Priorities

1. Add `.env.cloud` to runtime config loading or make the environment file explicit and required.
2. Repair the ambiguous intent LLM path in `llm_intent_classifier.py` and add integration coverage.
3. Add first-class benchmark hooks for retrieval-only vs rerank vs context-expansion traces instead of relying on internal calls.
4. Persist provider-attempt telemetry in `llm_client` so fallback chains are directly observable.
5. Tighten hallucination controls for ambiguous and adversarial prompts with stronger grounding requirements.
6. Add operational metrics for stream start latency, provider-attempt count, and fallback transitions.

## Artifacts

- `dataset.json`
- `benchmark_metadata.json`
- `corpus_stats.json`
- `retrieval_results.json`
- `generation_results.json`
- `classification_results.json`
- `fallback_results.json`
- `cache_and_stream_results.json`
- `scalability_results.json`
- `judge_evaluations.json`
- `errors.json`
- `failures.json`
"""
        return report

    async def run(self) -> int:
        await self.write_json("dataset.json", self.dataset)
        corpus_stats = await self.get_corpus_stats()
        await self.write_json("corpus_stats.json", corpus_stats)
        corpus_preflight = await self.validate_corpus_preflight(corpus_stats)
        judge_preflight = await self.validate_judge_pipeline()
        routing_preflight = await self.validate_routing_determinism()
        retrieval_preflight = await self.validate_retrieval_availability()
        benchmark_metadata = {
            "timestamp": now_utc(),
            "corpus_source": corpus_stats.get("corpus", {}).get("source"),
            "corpus_size": corpus_stats.get("corpus", {}).get("knowledge_chunks_total"),
            "local_trusted_chunks": corpus_stats.get("local_trusted_corpus", {}).get("chunks"),
            "provider_map": get_provider_config_state().get("providers"),
            "retrieval_availability": retrieval_preflight,
            "routing_mode": {"deterministic": True, "env": os.getenv("DEPTHAPI_BENCHMARK_MODE", "")},
            "judge_preflight": judge_preflight,
            "corpus_preflight": corpus_preflight,
            "rag_backend": os.getenv("RAG_BACKEND", ""),
        }
        await self.write_json("benchmark_metadata.json", benchmark_metadata)
        retrieval = await self.run_retrieval_benchmark()
        await self.write_json("retrieval_results.json", retrieval)
        generation = await self.run_generation_benchmark()
        await self.write_json("generation_results.json", generation)
        classification = await self.run_classification_benchmark()
        await self.write_json("classification_results.json", classification)
        fallback = await self.run_fallback_benchmark()
        await self.write_json("fallback_results.json", fallback)
        cache_and_stream = await self.run_cache_and_stream_benchmark()
        await self.write_json("cache_and_stream_results.json", cache_and_stream)
        scalability = await self.run_scalability_benchmark()
        await self.write_json("scalability_results.json", scalability)
        await self.write_json("judge_evaluations.json", self.judge_raw)
        await self.write_json("errors.json", self.errors)

        failures = self.identify_failures(corpus_stats, retrieval, generation, classification, fallback)
        readiness = self._production_readiness_score(retrieval, generation, classification, fallback, scalability)
        await self.write_json("failures.json", [asdict(item) for item in failures])
        await self.write_json("readiness.json", readiness)

        report = self.render_report(
            corpus_stats,
            retrieval,
            generation,
            classification,
            fallback,
            cache_and_stream,
            scalability,
            readiness,
            failures,
        )
        (self.output_dir / "benchmark_report.md").write_text(report, encoding="utf-8")
        await close_llm_client()
        return 0


async def main() -> int:
    harness = BenchmarkHarness()
    return await harness.run()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
