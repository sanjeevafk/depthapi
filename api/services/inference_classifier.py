"""Intent and query-shape classification extracted from inference orchestration."""

from __future__ import annotations

from services.intent import detect_diagram_type, detect_intent_and_depth


class IntentClassifier:
    def detect_intent(self, query: str, context: dict | None = None) -> tuple[str, float]:
        _ = context
        result = detect_intent_and_depth(query)
        intent = str(result.get("intent", "explain"))
        confidence = 0.9
        return intent, confidence

    def detect_depth(self, query: str) -> str:
        result = detect_intent_and_depth(query)
        return str(result.get("depth", "medium"))

    def detect_intent_and_depth(self, query: str) -> dict[str, str]:
        result = detect_intent_and_depth(query)
        return {
            "intent": str(result.get("intent", "explain")),
            "depth": str(result.get("depth", "medium")),
        }

    def detect_diagram_type(self, query: str) -> str | None:
        return detect_diagram_type(query)
