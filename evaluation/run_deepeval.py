import os
import time
from typing import Dict, Any, List

from eval_utils import MAX_RETRIES, log_eval_failure


def evaluate_deepeval(query: str, answer: str, context: List[str], sample_id: str = None) -> Dict[str, Any]:
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
    from deepeval.models.base_model import DeepEvalBaseLLM
    from langchain_groq import ChatGroq

    model_name = os.environ.get("EVALUATOR_MODEL", "llama-3.3-70b-versatile")

    class EvalLLM(DeepEvalBaseLLM):
        def __init__(self):
            self.llm = ChatGroq(model=model_name, temperature=0, max_retries=3)

        def load_model(self):
            return self.llm

        def generate(self, prompt: str) -> str:
            return self.llm.invoke(prompt).content

        async def a_generate(self, prompt: str) -> str:
            return (await self.llm.ainvoke(prompt)).content

        def get_model_name(self):
            return model_name

    retrieval_context = [str(x) for x in (context or []) if x is not None]
    case = LLMTestCase(input=query, actual_output=answer, retrieval_context=retrieval_context)
    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            rel = AnswerRelevancyMetric(threshold=0.5, model=EvalLLM())
            faith = FaithfulnessMetric(threshold=0.5, model=EvalLLM())
            rel.measure(case)
            faith.measure(case)
            return {"deepeval_relevancy": rel.score, "deepeval_faithfulness": faith.score}
        except Exception as exc:
            last_error = str(exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(1.0 * (2 ** attempt))

    log_eval_failure(
        evaluator="deepeval",
        metric_name="deepeval_relevancy,deepeval_faithfulness",
        prompt_name="deepeval_internal",
        sample_id=sample_id,
        model=model_name,
        retry_count=MAX_RETRIES,
        exception=last_error,
        raw_response="",
    )
    return {"deepeval_relevancy": None, "deepeval_faithfulness": None, "error": "EVAL_FAILED"}
