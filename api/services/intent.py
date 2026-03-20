import re
from typing import Literal

IntentType = Literal["explain", "compare", "brainstorm"]
DepthType = Literal["shallow", "medium", "deep"]

# Intent patterns - first match wins, order is priority
INTENT_PATTERNS: list[tuple[IntentType, list[str]]] = [
    (
        "compare",
        [
            r"\bcompare\b",
            r"\bvs\b",
            r"\bversus\b",
            r"\bdifference between\b",
            r"\bpros and cons\b",
            r"\btradeoffs\b",
        ],
    ),
    (
        "brainstorm",
        [
            r"\barchitecture\b",
            r"\bdesign\b",
            r"\bideas\b",
            r"\bapproaches\b",
            r"\bways to\b",
            r"\bhow (would|could|should) (i|we|you)\b",
            r"\bshould i\b",
            r"\bwhich approach\b",
        ],
    ),
    ("explain", []),
]

# Depth patterns - first match wins, order is priority
DEPTH_PATTERNS: list[tuple[DepthType, list[str]]] = [
    (
        "deep",
        [
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
        ],
    ),
    (
        "shallow",
        [
            r"\bwhat is\b",
            r"\bdefine\b",
            r"\boverview\b",
            r"\bbriefly\b",
            r"\bsimply\b",
            r"\beli5\b",
            r"\bsummary\b",
        ],
    ),
    ("medium", []),
]


def detect_intent_and_depth(query: str) -> dict:
    """
    Deterministic heuristic classifier. No LLM. <1ms.
    Intent and depth are detected independently so combinations
    like "compare in depth" return {"intent": "compare", "depth": "deep"}.
    Returns: {"intent": IntentType, "depth": DepthType}
    """
    lowered = query.lower().strip()

    intent: IntentType = "explain"
    for intent_name, patterns in INTENT_PATTERNS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            intent = intent_name
            break

    depth: DepthType = "medium"
    for depth_name, patterns in DEPTH_PATTERNS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            depth = depth_name
            break

    return {"intent": intent, "depth": depth}


# Ordered: more specific keywords first
DIAGRAM_TRIGGERS: list[tuple[str, str]] = [
    ("er diagram", "erDiagram"),
    ("class diagram", "classDiagram"),
    ("state machine", "stateDiagram-v2"),
    ("state diagram", "stateDiagram-v2"),
    ("transitions", "stateDiagram-v2"),
    ("sequence", "sequenceDiagram"),
    ("request flow", "sequenceDiagram"),
    ("pipeline", "flowchart"),
    ("architecture", "flowchart"),
    ("flow", "flowchart"),
    ("process", "flowchart"),
    ("steps", "flowchart"),
    ("timeline", "timeline"),
]


def detect_diagram_type(query: str) -> str | None:
    """
    Returns a mermaid diagram type string if the query benefits from
    a visual, otherwise None. <1ms.
    """
    lowered = query.lower().strip()
    for keyword, diagram_type in DIAGRAM_TRIGGERS:
        if keyword in lowered:
            return diagram_type
    return None


DEFAULT_STRUCTURE_HEADERS = [
    "## Core Idea",
    "## First Principles Breakdown",
    "## Intuition",
    "## Edge Cases / Limitations",
    "## Connections",
]

COMPARE_STRUCTURE_HEADERS = [
    "## Option A",
    "## Option B",
    "## Key Differences",
    "## Recommendation",
]

BRAINSTORM_STRUCTURE_HEADERS = [
    "## Approach 1",
    "## Approach 2",
    "## Approach 3",
]

VALID_TERMINAL_CHARS = {".", "?", "!", "`"}
MIN_RESPONSE_LENGTH = 150
MIN_DEFAULT_HEADERS = 3
MIN_COMPARE_HEADERS = 3
MIN_BRAINSTORM_HEADERS = 2


def validate_technical_response(response: str, intent: str) -> tuple[bool, str]:
    """
    Returns (is_valid: bool, reason: str).
    reason is "" when valid, machine-readable failure code when invalid.

    Failure codes: "empty", "too_short", "truncated",
                   "missing_structure", "missing_brainstorm_structure",
                   "missing_compare_structure"
    """
    if not response or not response.strip():
        return False, "empty"

    stripped = response.strip()

    if stripped[-1] not in VALID_TERMINAL_CHARS:
        return False, "truncated"

    if intent == "brainstorm":
        present = sum(1 for header in BRAINSTORM_STRUCTURE_HEADERS if header in response)
        if present < MIN_BRAINSTORM_HEADERS:
            return False, "missing_brainstorm_structure"
        if len(stripped) < MIN_RESPONSE_LENGTH:
            return False, "too_short"
    elif intent == "compare":
        present = sum(1 for header in COMPARE_STRUCTURE_HEADERS if header in response)
        if present < MIN_COMPARE_HEADERS:
            return False, "missing_compare_structure"
        if len(stripped) < MIN_RESPONSE_LENGTH:
            return False, "too_short"
    else:
        if len(stripped) < MIN_RESPONSE_LENGTH:
            return False, "too_short"
        present = sum(1 for header in DEFAULT_STRUCTURE_HEADERS if header in response)
        if present < MIN_DEFAULT_HEADERS:
            return False, "missing_structure"

    return True, ""
