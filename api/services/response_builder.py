"""Mode-specific response formatting and fallbacks."""

from __future__ import annotations

from api.services.inference_socratic import (
    _enforce_socratic_response_constraints,
    _fallback_socratic_question,
)


class ResponseBuilder:
    def build_response(self, llm_output: str, mode: str, query: str) -> str:
        if mode == "socratic":
            return self.apply_socratic_fallback(query, llm_output)
        if mode == "learn":
            return self.apply_learning_mode_formatting(llm_output)
        return (llm_output or "").strip()

    def apply_socratic_fallback(self, query: str, response: str | None = None) -> str:
        text = (response or "").strip()
        if not text:
            text = _fallback_socratic_question(query)
        return _enforce_socratic_response_constraints(text, topic=query, wants_direct_answer=False)

    def apply_learning_mode_formatting(self, response: str) -> str:
        return "\n\n".join(part.strip() for part in str(response or "").split("\n\n") if part.strip())
