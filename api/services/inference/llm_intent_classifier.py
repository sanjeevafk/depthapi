"""Hybrid intent classifier for DepthAPI query routing.

Strategy
--------
1. Regex pass  (<1 ms, zero cost)   — resolves ~75% of queries confidently
2. SLM fallback (~150 ms, Groq)     — handles ambiguous or complex phrasing
3. Redis cache  (TTL 1 hr)          — SLM results cached by query hash

The SLM fallback is only triggered when the regex pass produces a low-confidence
result (no strong keyword signal). All callers receive the same dict shape:

    {
        "intent":    "explain" | "compare" | "brainstorm",
        "depth":     "shallow" | "medium" | "deep",
        "source":    "regex"   | "llm",
        "confidence": float,   # 0.0–1.0; regex uses heuristic score
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Literal

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

IntentType  = Literal["explain", "compare", "brainstorm"]
DepthType   = Literal["shallow", "medium", "deep"]
ClassifierSource = Literal["regex", "llm"]


class IntentResult:
    __slots__ = ("intent", "depth", "source", "confidence")

    def __init__(
        self,
        intent: IntentType,
        depth: DepthType,
        source: ClassifierSource,
        confidence: float,
    ) -> None:
        self.intent = intent
        self.depth = depth
        self.source = source
        self.confidence = confidence

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "depth": self.depth,
            "source": self.source,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Regex patterns  (first match wins; order = priority)
# ---------------------------------------------------------------------------

_COMPARE_PATTERNS: list[str] = [
    r"\bcompare\b",
    r"\bvs\b",
    r"\bversus\b",
    r"\bdifference between\b",
    r"\bpros and cons\b",
    r"\btradeoffs?\b",
    r"\bcontrast\b",
]

_BRAINSTORM_PATTERNS: list[str] = [
    r"\barchitecture\b",
    r"\bdesign\b",
    r"\bideas?\b",
    r"\bapproaches?\b",
    r"\bways to\b",
    r"\bhow (would|could|should) (i|we|you)\b",
    r"\bshould i\b",
    r"\bwhich approach\b",
]

_DEEP_PATTERNS: list[str] = [
    r"\bin depth\b",
    r"\bdeep dive\b",
    r"\bderive\b",
    r"\bintuition\b",
    r"\bfrom scratch\b",
    r"\bmathematically\b",
    r"\brigorously\b",
    r"\bfrom first principles\b",
    r"\bunder the hood\b",
    r"\bhow exactly\b",
]

_SHALLOW_PATTERNS: list[str] = [
    r"\bwhat is\b",
    r"\bdefine\b",
    r"\boverview\b",
    r"\bsimply\b",
    r"\bsimple\b",
    r"\bsummary\b",
    r"\btldr\b",
    r"\bbriefly\b",
]

# Minimum number of pattern hits to be considered "confident"
_CONFIDENCE_THRESHOLD = 1


def _count_hits(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text))


def _regex_classify(query: str) -> IntentResult:
    """Deterministic regex classifier. Always returns a result; confidence
    reflects how many keyword signals fired."""
    lowered = query.lower().strip()

    # --- Intent ---
    compare_hits    = _count_hits(lowered, _COMPARE_PATTERNS)
    brainstorm_hits = _count_hits(lowered, _BRAINSTORM_PATTERNS)

    if compare_hits >= _CONFIDENCE_THRESHOLD and compare_hits >= brainstorm_hits:
        intent: IntentType = "compare"
        intent_hits = compare_hits
    elif brainstorm_hits >= _CONFIDENCE_THRESHOLD:
        intent = "brainstorm"
        intent_hits = brainstorm_hits
    else:
        intent = "explain"
        intent_hits = 0  # default; no strong signal needed

    # --- Depth ---
    deep_hits    = _count_hits(lowered, _DEEP_PATTERNS)
    shallow_hits = _count_hits(lowered, _SHALLOW_PATTERNS)

    if deep_hits >= _CONFIDENCE_THRESHOLD:
        depth: DepthType = "deep"
        depth_hits = deep_hits
    elif shallow_hits >= _CONFIDENCE_THRESHOLD:
        depth = "shallow"
        depth_hits = shallow_hits
    else:
        depth = "medium"
        depth_hits = 0

    # Confidence: normalised hit count across both dimensions
    total_hits = intent_hits + depth_hits
    confidence = min(1.0, total_hits * 0.25)  # 4+ signals → 1.0

    return IntentResult(intent=intent, depth=depth, source="regex", confidence=confidence)


def _is_ambiguous(result: IntentResult) -> bool:
    """Return True if the regex result is weak enough to warrant an SLM call."""
    return result.confidence < 0.25  # no strong keyword signal in either dimension


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM = (
    "You are a query-classification engine for a developer knowledge API. "
    "You output only valid JSON. No prose, no markdown, no explanation."
)

_CLASSIFIER_PROMPT_TEMPLATE = """\
Classify this API query. Output ONLY a JSON object with these exact keys:

{{
  "intent":    "explain" | "compare" | "brainstorm",
  "depth":     "shallow" | "medium" | "deep"
}}

Definitions:
- compare:    asks to contrast ≥2 things (vs, difference, tradeoffs, pros/cons)
- brainstorm: asks for options / design / approaches (how would I, which approach)
- explain:    everything else (what is, how does, walk me through)
- shallow:    high-level overview; user wants a quick answer
- deep:       mechanistic detail; user uses words like: in depth, derive, rigorously
- medium:     everything in between

Query: "{query}"
"""


def _build_classifier_prompt(query: str) -> str:
    escaped = query.replace('"', '\\"').replace("\n", " ").strip()[:400]
    return _CLASSIFIER_PROMPT_TEMPLATE.format(query=escaped)


async def _call_llm_classifier(query: str) -> IntentResult:
    """Call the fast model alias to classify an ambiguous query."""
    # Import inline to avoid circular dependency at module load time
    from api.services.model_client import call_model_raw  # type: ignore[import]

    prompt = _build_classifier_prompt(query)
    try:
        raw = await call_model_raw(
            system=_CLASSIFIER_SYSTEM,
            user=prompt,
            model_alias="learn-groq-llama8b",  # fast + cheap
            max_tokens=32,
            temperature=0.0,
        )
        data = json.loads(raw.strip())
        intent  = data.get("intent",  "explain")
        depth   = data.get("depth",   "medium")
        # Validate values fall in allowed set
        if intent not in ("explain", "compare", "brainstorm"):
            intent = "explain"
        if depth not in ("shallow", "medium", "deep"):
            depth = "medium"
        return IntentResult(intent=intent, depth=depth, source="llm", confidence=0.90)
    except Exception as exc:
        _logger.warning("llm_intent_classifier_failed error=%s", exc)
        # Graceful degradation: return a safe default
        return IntentResult(intent="explain", depth="medium", source="llm", confidence=0.50)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_CACHE_TTL = 3600  # 1 hour
_CACHE_PREFIX = "depthapi:intent:"


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(query.lower().strip().encode()).hexdigest()[:16]
    return f"{_CACHE_PREFIX}{digest}"


async def _read_cache(query: str) -> IntentResult | None:
    try:
        from api.services.infra.cache import get_cache_client  # type: ignore[import]
        client = await get_cache_client()
        raw = await client.get(_cache_key(query))
        if raw:
            data = json.loads(raw)
            return IntentResult(**data)
    except Exception:
        pass
    return None


async def _write_cache(query: str, result: IntentResult) -> None:
    try:
        from api.services.infra.cache import get_cache_client  # type: ignore[import]
        client = await get_cache_client()
        await client.set(_cache_key(query), json.dumps(result.to_dict()), ex=_CACHE_TTL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def classify_intent(query: str, *, use_llm: bool = True) -> IntentResult:
    """Classify intent and depth for the given query.

    Parameters
    ----------
    query:
        Raw user query string.
    use_llm:
        Set to False to force regex-only mode (useful in tests / high-volume batch).

    Returns
    -------
    IntentResult with .intent, .depth, .source, .confidence
    """
    # 1. Regex fast path
    result = _regex_classify(query)
    if not _is_ambiguous(result) or not use_llm:
        return result

    # 2. Check cache for this ambiguous query
    cached = await _read_cache(query)
    if cached is not None:
        return cached

    # 3. SLM fallback
    result = await _call_llm_classifier(query)

    # 4. Write to cache
    await _write_cache(query, result)

    return result
