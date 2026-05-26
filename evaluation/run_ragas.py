from typing import Dict, Any, List, Optional

from eval_utils import EVALUATOR_MODEL, MAX_RETRIES, call_evaluator_model, log_eval_failure, parse_json_with_repair


def evaluate_ragas(query: str, answer: str, context: List[str], sample_id: Optional[str] = None) -> Dict[str, Any]:
    """Ragas-style evaluator with explicit structured-output control.

    The pinned Ragas package remains installed, but its internal parser path
    hangs/fails with current Gemini responses. This wrapper preserves the two
    Ragas metrics used by the report while enforcing our parse/repair/logging
    contract directly.

    Retry policy: up to MAX_RETRIES attempts (inherited from eval_utils).
    On 429s the outer loop detects the error string and waits longer before
    the next attempt; the inner call_evaluator_model also retries independently.
    """
    import time
    import os

    model_name = os.environ.get("EVALUATOR_MODEL", EVALUATOR_MODEL)
    prompt = f"""Return strict JSON only:
{{
  "ragas_answer_relevancy": number between 0 and 1,
  "ragas_faithfulness": number between 0 and 1
}}

Score answer_relevancy by how directly the answer addresses the question.
Score faithfulness by whether the answer is supported by the provided contexts.

Question: {query}
Answer: {answer}
Contexts: {context}
"""
    raw = ""
    last_exc_str = ""
    for attempt in range(MAX_RETRIES):
        try:
            raw = call_evaluator_model(prompt, json_mode=True, model=model_name)
            parsed = parse_json_with_repair(raw)
            if not parsed:
                raise ValueError("JSON parse failure")
            return {
                "ragas_answer_relevancy": parsed.get("ragas_answer_relevancy"),
                "ragas_faithfulness": parsed.get("ragas_faithfulness"),
            }
        except Exception as e:
            last_exc_str = str(e)
            if attempt >= MAX_RETRIES - 1:
                log_eval_failure(
                    evaluator="ragas",
                    metric_name="ragas_answer_relevancy,ragas_faithfulness",
                    prompt_name="ragas_structured_direct",
                    sample_id=sample_id,
                    model=model_name,
                    retry_count=attempt + 1,
                    exception=last_exc_str,
                    raw_response=raw,
                )
                return {"ragas_answer_relevancy": None, "ragas_faithfulness": None, "error": "EVAL_FAILED"}
            # Detect 429 rate-limit and wait longer before the next attempt.
            if "429" in last_exc_str or "rate limit" in last_exc_str.lower():
                backoff = min(60.0, 4.0 * (2 ** attempt))
            else:
                backoff = 1.0 * (2 ** attempt)
            time.sleep(backoff)

    return {"ragas_answer_relevancy": None, "ragas_faithfulness": None, "error": "EVAL_FAILED"}
