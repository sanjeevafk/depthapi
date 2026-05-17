"""Hybrid intent classifier for DepthAPI prompt-axis routing.

Strategy
--------
1. Regex pass  (<1 ms, zero cost)   — resolves ~75% of queries confidently
2. SLM fallback (~150 ms, Groq)     — handles ambiguous or complex phrasing
3. Redis cache  (TTL 1 hr)          — SLM results cached by query hash

The SLM fallback is only triggered when the regex pass produces a low-confidence
result (no strong keyword signal). All callers receive the same dict shape:

    {
        "task":       "explain" | "compare" | "brainstorm" | "analyze" | "summarize",
        "depth":      "simple" | "accessible" | "technical" | "expert",
        "reasoning":  "direct" | "socratic" | "debate" | "guided",
        "style":      "normal" | "meme" | "concise" | "academic",
        "capabilities": ["requires_diagram", ...],
        "source":     "regex" | "llm",
        "confidence": float
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Literal

from api.prompt_engine import PromptSpec

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TaskType = Literal["explain", "compare", "brainstorm", "analyze", "summarize"]
DepthType = Literal["simple", "accessible", "technical", "expert"]
ReasoningType = Literal["direct", "socratic", "debate", "guided"]
StyleType = Literal["normal", "meme", "concise", "academic"]
ClassifierSource = Literal["regex", "llm"]


class IntentResult:
    __slots__ = ("task", "depth", "reasoning", "style", "capabilities", "source", "confidence")

    def __init__(
        self,
        task: TaskType | None = None,
        depth: DepthType = "accessible",
        reasoning: ReasoningType = "direct",
        style: StyleType = "normal",
        capabilities: list[str] | None = None,
        source: ClassifierSource = "regex",
        confidence: float = 0.0,
        intent: TaskType | None = None,
    ) -> None:
        self.task = task or intent or "explain"
        self.depth = depth
        self.reasoning = reasoning
        self.style = style
        self.capabilities = capabilities or []
        self.source = source
        self.confidence = confidence

    @property
    def intent(self) -> TaskType:
        return self.task

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "intent": self.task,
            "depth": self.depth,
            "reasoning": self.reasoning,
            "style": self.style,
            "capabilities": self.capabilities,
            "source": self.source,
            "confidence": self.confidence,
        }

    def to_prompt_spec(self, topic: str) -> PromptSpec:
        return PromptSpec(
            topic=topic,
            depth=self.depth,
            task=self.task,
            reasoning=self.reasoning,
            style=self.style,
            capabilities=frozenset(self.capabilities),
        )


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

_EXPERT_PATTERNS: list[str] = [
    r"\bexpert\b",
    r"\bresearch level\b",
    r"\bformal proof\b",
    r"\bpeer review\b",
]

_TECHNICAL_PATTERNS: list[str] = [
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
    r"\btechnical\b",
    r"\bimplementation\b",
]

_SIMPLE_PATTERNS: list[str] = [
    r"\bwhat is\b",
    r"\bdefine\b",
    r"\boverview\b",
    r"\bsimply\b",
    r"\bsimple\b",
    r"\bsummary\b",
    r"\btldr\b",
    r"\bbriefly\b",
]

_SUMMARIZE_PATTERNS: list[str] = [
    r"\bsummarize\b",
    r"\bsummary\b",
    r"\btldr\b",
    r"\btl;dr\b",
]

_SOCRATIC_PATTERNS: list[str] = [
    r"\bsocratic\b",
    r"\bquiz me\b",
    r"\bask me\b",
    r"\bguide me with questions\b",
]

_GUIDED_PATTERNS: list[str] = [
    r"\bstep by step\b",
    r"\bwalk me through\b",
    r"\bguide me\b",
]

_DEBATE_PATTERNS: list[str] = [
    r"\bdebate\b",
    r"\bargue both sides\b",
    r"\bcase for and against\b",
]

_MEME_PATTERNS: list[str] = [
    r"\bmeme\b",
    r"\bfunny\b",
]

_ACADEMIC_PATTERNS: list[str] = [
    r"\bacademic\b",
    r"\bcitations?\b",
    r"\bsources?\b",
]

_DIAGRAM_PATTERNS: list[str] = [
    r"\bdiagram\b",
    r"\bflowchart\b",
    r"\bsequence\b",
    r"\barchitecture\b",
    r"\bpipeline\b",
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
        task: TaskType = "compare"
        task_hits = compare_hits
    elif brainstorm_hits >= _CONFIDENCE_THRESHOLD:
        task = "brainstorm"
        task_hits = brainstorm_hits
    elif _count_hits(lowered, _SUMMARIZE_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        task = "summarize"
        task_hits = _count_hits(lowered, _SUMMARIZE_PATTERNS)
    else:
        task = "explain"
        task_hits = 0  # default; no strong signal needed

    # --- Depth ---
    expert_hits = _count_hits(lowered, _EXPERT_PATTERNS)
    technical_hits = _count_hits(lowered, _TECHNICAL_PATTERNS)
    simple_hits = _count_hits(lowered, _SIMPLE_PATTERNS)

    if expert_hits >= _CONFIDENCE_THRESHOLD:
        depth: DepthType = "expert"
        depth_hits = expert_hits
    elif technical_hits >= _CONFIDENCE_THRESHOLD:
        depth = "technical"
        depth_hits = technical_hits
    elif simple_hits >= _CONFIDENCE_THRESHOLD:
        depth = "simple"
        depth_hits = simple_hits
    else:
        depth = "accessible"
        depth_hits = 0

    reasoning: ReasoningType = "direct"
    reasoning_hits = 0
    if _count_hits(lowered, _SOCRATIC_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        reasoning = "socratic"
        reasoning_hits = _count_hits(lowered, _SOCRATIC_PATTERNS)
    elif _count_hits(lowered, _DEBATE_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        reasoning = "debate"
        reasoning_hits = _count_hits(lowered, _DEBATE_PATTERNS)
    elif _count_hits(lowered, _GUIDED_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        reasoning = "guided"
        reasoning_hits = _count_hits(lowered, _GUIDED_PATTERNS)

    style: StyleType = "normal"
    style_hits = 0
    if _count_hits(lowered, _MEME_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        style = "meme"
        style_hits = _count_hits(lowered, _MEME_PATTERNS)
    elif _count_hits(lowered, _ACADEMIC_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        style = "academic"
        style_hits = _count_hits(lowered, _ACADEMIC_PATTERNS)
    elif task == "summarize" or "brief" in lowered or "concise" in lowered:
        style = "concise"

    capabilities = []
    if _count_hits(lowered, _DIAGRAM_PATTERNS) >= _CONFIDENCE_THRESHOLD:
        capabilities.append("requires_diagram")
    if "citation" in lowered or "sources" in lowered or "latest" in lowered:
        capabilities.extend(["requires_search", "requires_citations"])

    # Confidence: normalised hit count across both dimensions
    total_hits = task_hits + depth_hits + reasoning_hits + style_hits
    confidence = min(1.0, total_hits * 0.25)  # 4+ signals → 1.0

    return IntentResult(
        task=task,
        depth=depth,
        reasoning=reasoning,
        style=style,
        capabilities=capabilities,
        source="regex",
        confidence=confidence,
    )


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
  "task": "explain" | "compare" | "brainstorm" | "analyze" | "summarize",
  "depth": "simple" | "accessible" | "technical" | "expert",
  "reasoning": "direct" | "socratic" | "debate" | "guided",
  "style": "normal" | "meme" | "concise" | "academic",
  "capabilities": []
}}

Definitions:
- compare:    asks to contrast ≥2 things (vs, difference, tradeoffs, pros/cons)
- brainstorm: asks for options / design / approaches (how would I, which approach)
- explain:    everything else (what is, how does, walk me through)
- summarize:  asks for compressed restatement or TLDR
- simple:     plain-language overview; user wants a quick answer
- technical:  mechanistic detail; implementation, internals, or engineering depth
- expert:     peer-level, formal, research, or literature-level treatment
- accessible: everything in between
- capabilities may include: "requires_search", "requires_diagram", "requires_context", "requires_citations"

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
        task = data.get("task") or data.get("intent") or "explain"
        depth = data.get("depth", "accessible")
        reasoning = data.get("reasoning", "direct")
        style = data.get("style", "normal")
        capabilities = data.get("capabilities", [])
        # Validate values fall in allowed set
        if task not in ("explain", "compare", "brainstorm", "analyze", "summarize"):
            task = "explain"
        if depth not in ("simple", "accessible", "technical", "expert"):
            depth = "accessible"
        if reasoning not in ("direct", "socratic", "debate", "guided"):
            reasoning = "direct"
        if style not in ("normal", "meme", "concise", "academic"):
            style = "normal"
        if not isinstance(capabilities, list):
            capabilities = []
        capabilities = [
            item for item in capabilities
            if item in {"requires_search", "requires_diagram", "requires_context", "requires_citations"}
        ]
        return IntentResult(
            task=task,
            depth=depth,
            reasoning=reasoning,
            style=style,
            capabilities=capabilities,
            source="llm",
            confidence=0.90,
        )
    except Exception as exc:
        _logger.warning("llm_intent_classifier_failed error=%s", exc)
        # Graceful degradation: return a safe default
        return IntentResult(
            task="explain",
            depth="accessible",
            reasoning="direct",
            style="normal",
            capabilities=[],
            source="llm",
            confidence=0.50,
        )


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
    """Classify prompt axes for the given query.

    Parameters
    ----------
    query:
        Raw user query string.
    use_llm:
        Set to False to force regex-only mode (useful in tests / high-volume batch).

    Returns
    -------
    IntentResult with prompt-axis fields and .to_prompt_spec(topic)
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
