import asyncio
import os
from typing import Dict, Any, List

from eval_utils import EVALUATOR_MODEL, MAX_RETRIES, call_evaluator_model, log_eval_failure, parse_json_with_repair


class CustomLLMJudge:
    def __init__(self):
        self.model = os.environ.get("EVALUATOR_MODEL", EVALUATOR_MODEL)

    async def evaluate(self, query: str, answer: str, context: List[str], prompt_spec: Dict[str, Any] = None, sample_id: str = None) -> Dict[str, Any]:
        normalized_context = []
        for c in context or []:
            if isinstance(c, str):
                normalized_context.append(c)
            elif isinstance(c, dict):
                txt = c.get("text") or c.get("content")
                if txt:
                    normalized_context.append(str(txt))
        context_str = "\n\n".join(normalized_context)
        prompt = f"""Return strict JSON only.
{{
  "depth_compliance": 1-5 integer,
  "answer_quality": 1-5 integer,
  "citation_accuracy": 1-5 integer,
  "faithfulness": 1-5 integer,
  "reasoning": "brief string"
}}
Query: {query}
Prompt Spec: {prompt_spec}
Context: {context_str}
Answer: {answer}
"""
        for attempt in range(MAX_RETRIES):
            try:
                raw = call_evaluator_model(prompt, json_mode=True, model=self.model)
                parsed = parse_json_with_repair(raw)
                if not parsed:
                    raise ValueError("JSON parse failure")
                return {
                    "depth_compliance": parsed.get("depth_compliance"),
                    "answer_quality": parsed.get("answer_quality"),
                    "citation_accuracy": parsed.get("citation_accuracy"),
                    "faithfulness": parsed.get("faithfulness"),
                    "reasoning": parsed.get("reasoning"),
                }
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    log_eval_failure(
                        evaluator="judge",
                        metric_name="all",
                        prompt_name="judge_prompt",
                        sample_id=sample_id,
                        model=self.model,
                        retry_count=attempt + 1,
                        exception=str(e),
                        raw_response=locals().get("raw", ""),
                    )
                    return {
                        "depth_compliance": None,
                        "answer_quality": None,
                        "citation_accuracy": None,
                        "faithfulness": None,
                        "reasoning": "EVAL_FAILED",
                    }
                await asyncio.sleep(1.0 * (2 ** attempt))
