import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

EVAL_LOG_DIR = Path("results/eval_logs")
EVAL_LOG_DIR.mkdir(parents=True, exist_ok=True)

EVALUATOR_MODEL = "openai/gpt-4o-mini"
MAX_RETRIES = 4
_CALL_LOCK = threading.Lock()
_LAST_CALL_TS = 0.0


def call_evaluator_model(prompt: str, *, json_mode: bool = True, model: Optional[str] = None) -> str:
    import httpx

    provider = (os.environ.get("EVALUATOR_PROVIDER", "groq") or "groq").strip().lower()
    model_name = model or os.environ.get("EVALUATOR_MODEL", EVALUATOR_MODEL)

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is required when EVALUATOR_PROVIDER=openrouter")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    elif provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is required when EVALUATOR_PROVIDER=gemini")
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    elif provider == "groq":
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is required when EVALUATOR_PROVIDER=groq")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    else:
        raise RuntimeError(f"Unsupported EVALUATOR_PROVIDER: {provider}")

    # Global request pacing across judge/deepeval/ragas to reduce provider 429s.
    # Defaults are conservative for Groq free-tier (30 req/min limit).
    min_interval_s = float(os.environ.get("EVAL_CALL_DELAY_SECONDS", "1.5"))
    max_retries = int(os.environ.get("EVAL_HTTP_RETRIES", "4"))
    backoff_base = float(os.environ.get("EVAL_HTTP_BACKOFF_BASE", "2.0"))
    max_backoff_s = float(os.environ.get("EVAL_HTTP_MAX_BACKOFF_SECONDS", "60.0"))

    for attempt in range(max_retries):
        with _CALL_LOCK:
            global _LAST_CALL_TS
            now = time.time()
            wait_s = max(0.0, min_interval_s - (now - _LAST_CALL_TS))
            if wait_s > 0:
                time.sleep(wait_s)
            _LAST_CALL_TS = time.time()

        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            if attempt == max_retries - 1:
                raise RuntimeError(f"429 Exhausted: {resp.text}")
            # For 429s, use a longer wait to let the provider rate-limit window reset.
            wait_429 = min(max_backoff_s, backoff_base ** (attempt + 2))
            time.sleep(wait_429)
            continue
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    raise RuntimeError("Evaluator request failed after retries")


def log_eval_failure(
    *,
    evaluator: str,
    metric_name: str,
    prompt_name: str,
    sample_id: Optional[str],
    model: str,
    retry_count: int,
    exception: str,
    raw_response: str,
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evaluator": evaluator,
        "metric_name": metric_name,
        "prompt_name": prompt_name,
        "sample_id": sample_id,
        "provider_model": model,
        "retry_count": retry_count,
        "exception": exception,
        "raw_response": raw_response,
    }
    out = EVAL_LOG_DIR / f"{evaluator}_failures.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def extract_json_text(raw: str) -> Optional[str]:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return m.group(0) if m else None


def parse_json_with_repair(raw: str) -> Optional[Dict[str, Any]]:
    cand = extract_json_text(raw)
    if not cand:
        return None
    try:
        return json.loads(cand)
    except Exception:
        repaired = cand.replace("\n", " ").replace("\t", " ")
        repaired = re.sub(r",\s*}", "}", repaired)
        repaired = re.sub(r",\s*]", "]", repaired)
        try:
            return json.loads(repaired)
        except Exception:
            return None
