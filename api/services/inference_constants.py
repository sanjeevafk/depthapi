"""Shared constants for inference routing and prompting."""

TECHNICAL_MODEL_PRIMARY = "technical-primary"
TECHNICAL_MODEL_FALLBACK = "technical-fallback"
TECHNICAL_TEMPERATURE = 0.4
TECHNICAL_MAX_TOKENS = 2048

LEARNING_MODEL_SIMPLE = "default-fast"
LEARNING_MODEL_DETAILED = "learning-detailed"
LEARNING_DETAILED_LEVELS = {"expert", "meme"}

LEARN_GEMINI_FLASH_ALIAS = "learn-gemini-flash"
LEARN_GROQ_FAST_ALIAS = "learn-groq-llama8b"
LEARN_OPENROUTER_FALLBACK_ALIAS = "learn-openrouter-free"
TECH_GEMINI_FLASH_ALIAS = "technical-gemini-flash"
TECH_OPENROUTER_ALIAS = "technical-openrouter-free"
TECH_GROQ_FAST_ALIAS = "technical-groq-llama8b"
TECH_GEMINI_PRO_ALIAS = "technical-gemini-pro"
TECH_CEREBRAS_GLM_ALIAS = "technical-cerebras-glm"
SOCRATIC_OPENROUTER_ALIAS = "socratic-openrouter-free"
SOCRATIC_CEREBRAS_ALIAS = "socratic-cerebras-glm"
SOCRATIC_GEMINI_ALIAS = "socratic-gemini-pro"
SOCRATIC_GROQ_ALIAS = "socratic-groq-llama8b"

TECHNICAL_LAST_RESORT_RESPONSE = (
    "## Core Idea\n"
    "Unable to generate a response at this time. Please retry in a moment.\n\n"
    "## First Principles Breakdown\n"
    "The model service may be temporarily unavailable.\n\n"
    "## Intuition\n"
    "Retrying often resolves transient issues.\n\n"
    "## Edge Cases / Limitations\n"
    "If this persists, check service status or try a different query.\n\n"
    "## Connections\n"
    "No connections available - response generation failed."
)

TECHNICAL_MINIMAL_PROMPT = "Explain the topic with concise technical clarity."

MODEL_PROFILES: dict[str, dict[str, float]] = {
    LEARNING_MODEL_SIMPLE: {
        "complexity": 0.45,
        "reasoning": 0.45,
        "explanation": 0.60,
        "latency_priority": 0.95,
    },
    LEARNING_MODEL_DETAILED: {
        "complexity": 0.70,
        "reasoning": 0.78,
        "explanation": 0.72,
        "latency_priority": 0.70,
    },
    TECHNICAL_MODEL_PRIMARY: {
        "complexity": 0.95,
        "reasoning": 0.95,
        "explanation": 0.88,
        "latency_priority": 0.40,
    },
    TECHNICAL_MODEL_FALLBACK: {
        "complexity": 0.60,
        "reasoning": 0.62,
        "explanation": 0.65,
        "latency_priority": 0.80,
    },
}

COST_PENALTY: dict[str, float] = {
    LEARNING_MODEL_SIMPLE: 0.08,
    LEARNING_MODEL_DETAILED: 0.16,
    TECHNICAL_MODEL_PRIMARY: 0.24,
    TECHNICAL_MODEL_FALLBACK: 0.12,
}

SEARCH_CONTEXT_MAX_CHARS = 1800
SEARCH_CONTEXT_TIMEOUT_SECONDS = 3.5

LATENCY_KEYWORDS = (
    r"\bquick\b",
    r"\bsummary\b",
    r"\btldr\b",
    r"\bshort\b",
    r"\bfast\b",
)
COMPLEXITY_KEYWORDS = (
    r"\boptimi[sz]e\b",
    r"\bdistributed\b",
    r"\bconcurrency\b",
    r"\btrade[ -]?offs?\b",
    r"\barchitecture\b",
    r"\bscal\w+\b",
    r"\bproof\b",
    r"\bderive\b",
)
REASONING_KEYWORDS = (
    r"\bwhy\b",
    r"\bcompare\b",
    r"\bversus\b",
    r"\bshould\b",
    r"\bpros?\b",
    r"\bcons?\b",
    r"\bdecision\b",
)
EXPLANATION_KEYWORDS = (
    r"\bexplain\b",
    r"\bhow\b",
    r"\bwalk me through\b",
    r"\bintuition\b",
    r"\bexample\b",
)
