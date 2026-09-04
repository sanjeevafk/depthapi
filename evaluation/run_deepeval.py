import os
import random
import time
from typing import Any

from eval_utils import call_evaluator_model, log_eval_failure


def evaluate_deepeval(
    query: str,
    answer: str,
    context: list[str],
    sample_id: str = None,
) -> dict[str, Any]:
    """Run AnswerRelevancy + Faithfulness via DeepEval.

    The inner LLM is wired to ``call_evaluator_model`` so all traffic goes
    through the shared global rate-limiter, Groq routing, and adaptive
    exponential-backoff — identical to the RAGAS evaluator.
    """
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
    from deepeval.models.base_model import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase

    model_name = os.environ.get("EVALUATOR_MODEL", "llama-3.1-8b-instant")
    max_retries = int(os.environ.get("EVAL_HTTP_RETRIES", "4"))
    max_backoff_s = float(os.environ.get("EVAL_HTTP_MAX_BACKOFF_SECONDS", "60.0"))
    backoff_base = float(os.environ.get("EVAL_HTTP_BACKOFF_BASE", "2.0"))

    class _GroqEvalLLM(DeepEvalBaseLLM):
        """Thin DeepEval LLM adapter that proxies every call through
        ``call_evaluator_model``, inheriting its global pacing lock,
        per-provider routing, and 429-aware retry logic.
        """

        def load_model(self) -> Any:
            return None  # not used — we proxy through call_evaluator_model

        def generate(self, prompt: str) -> str:
            # call_evaluator_model already handles retries + pacing internally;
            # deepeval's own retry wrapping is a secondary safety net only.
            # DeepEval expects machine-parseable JSON from its internal prompts.
            # Enforce JSON mode to avoid "invalid JSON" evaluator failures.
            return call_evaluator_model(prompt, json_mode=True, model=model_name)

        async def a_generate(self, prompt: str) -> str:
            # Deepeval may call async variant; delegate to sync (safe inside
            # asyncio.to_thread which runs in a worker thread).
            return self.generate(prompt)

        def get_model_name(self) -> str:
            return model_name

    retrieval_context = [str(x) for x in (context or []) if x is not None]
    case = LLMTestCase(
        input=query,
        actual_output=answer,
        retrieval_context=retrieval_context,
    )

    last_error = ""
    for attempt in range(max_retries):
        try:
            evaluator = _GroqEvalLLM()
            rel = AnswerRelevancyMetric(threshold=0.5, model=evaluator)
            faith = FaithfulnessMetric(threshold=0.5, model=evaluator)
            rel.measure(case)
            faith.measure(case)
            return {
                "deepeval_relevancy": rel.score,
                "deepeval_faithfulness": faith.score,
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt >= max_retries - 1:
                break

            # Differentiate 429s (long back-off) from other transient errors.
            if "429" in last_error or "rate limit" in last_error.lower():
                backoff = min(max_backoff_s, backoff_base ** (attempt + 2))
            else:
                backoff = min(max_backoff_s, backoff_base ** attempt)

            # Add jitter to avoid thundering-herd with concurrent workers.
            jitter = random.uniform(0.0, 0.5)
            time.sleep(backoff + jitter)

    log_eval_failure(
        evaluator="deepeval",
        metric_name="deepeval_relevancy,deepeval_faithfulness",
        prompt_name="deepeval_internal",
        sample_id=sample_id,
        model=model_name,
        retry_count=max_retries,
        exception=last_error,
        raw_response="",
    )
    return {
        "deepeval_relevancy": None,
        "deepeval_faithfulness": None,
        "error": "EVAL_FAILED",
    }
