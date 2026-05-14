"""Intent and diagram classification — async hybrid classifier.

Primary path:  classify_intent() from llm_intent_classifier
               (regex fast path + Groq SLM fallback for ambiguous queries)
Sync shim:     _sync_classify() wraps the async classifier for callers
               that cannot yet be made async (technical_mode, inference_routing).
               Falls back to pure regex if no running event loop is available.
"""

from __future__ import annotations

import asyncio
import logging

from api.services.conversation.intent import detect_diagram_type, detect_intent_and_depth
from api.services.inference.llm_intent_classifier import classify_intent

_logger = logging.getLogger(__name__)


def _sync_classify(query: str) -> dict[str, str]:
    """Run the async classifier synchronously.

    Tries to schedule classify_intent on the running event loop.
    Falls back to the regex-only classifier if that fails (e.g., no loop).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We are inside an async context — use a thread-safe future
            import concurrent.futures
            future: concurrent.futures.Future[dict] = concurrent.futures.Future()

            async def _run():
                result = await classify_intent(query)
                future.set_result(result.to_dict())

            asyncio.ensure_future(_run())
            # Can't block — fall back to regex for this sync call
            raise RuntimeError("async loop running; using regex fallback")
        else:
            result = loop.run_until_complete(classify_intent(query))
            return result.to_dict()
    except Exception as exc:
        _logger.debug("sync_classify_fallback reason=%s", exc)
        return detect_intent_and_depth(query)


class IntentClassifier:
    """Facade over the hybrid intent classifier.

    Async callers should use classify_async() directly.
    Sync callers (legacy integration points) use detect_intent_and_depth()
    which transparently routes to the regex fast path.
    """

    async def classify_async(self, query: str) -> dict[str, str]:
        """Full async path: regex + optional SLM fallback."""
        result = await classify_intent(query)
        return result.to_dict()

    def detect_intent(self, query: str, context: dict | None = None) -> tuple[str, float]:
        _ = context
        result = _sync_classify(query)
        intent = str(result.get("intent", "explain"))
        confidence = float(result.get("confidence", 0.5))
        return intent, confidence

    def detect_depth(self, query: str) -> str:
        result = _sync_classify(query)
        return str(result.get("depth", "medium"))

    def detect_intent_and_depth(self, query: str) -> dict[str, str]:
        result = _sync_classify(query)
        return {
            "intent": str(result.get("intent", "explain")),
            "depth":  str(result.get("depth", "medium")),
        }

    def detect_diagram_type(self, query: str) -> str | None:
        return detect_diagram_type(query)
