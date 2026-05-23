from typing import Dict, Any, List
import os

def evaluate_deepeval(query: str, answer: str, context: List[str]) -> Dict[str, Any]:
    """
    Run DeepEval metrics.
    Requires deepeval package. Configures custom ChatGroq model to avoid OpenAI requirement.
    """
    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
        from deepeval.models.base_model import DeepEvalBaseLLM
        from langchain_groq import ChatGroq
        
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return {"deepeval_relevancy": 0.0, "deepeval_faithfulness": 0.0, "error": "GROQ_API_KEY not set"}
            
        import time
        import random
        import asyncio

        class GroqLlama(DeepEvalBaseLLM):
            def __init__(self, model_name="llama-3.3-70b-versatile"):
                self.model_name = model_name
                self.llm = ChatGroq(model=model_name, temperature=0, api_key=api_key, max_retries=5)
            def load_model(self):
                return self.llm
            def _execute_with_retry(self, fn, *args, **kwargs):
                retries = 5
                delay = 2.0
                for attempt in range(retries):
                    try:
                        return fn(*args, **kwargs)
                    except Exception as e:
                        err_str = str(e)
                        if attempt == retries - 1:
                            raise
                        sleep_time = delay * (2 ** attempt) + random.uniform(0.1, 1.0)
                        print(f"[DeepEval] Error occurred: {err_str}. Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
            async def _a_execute_with_retry(self, fn, *args, **kwargs):
                retries = 5
                delay = 2.0
                for attempt in range(retries):
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as e:
                        err_str = str(e)
                        if attempt == retries - 1:
                            raise
                        sleep_time = delay * (2 ** attempt) + random.uniform(0.1, 1.0)
                        print(f"[DeepEval Async] Error occurred: {err_str}. Retrying in {sleep_time:.2f}s...")
                        await asyncio.sleep(sleep_time)
            def generate(self, prompt: str) -> str:
                res = self._execute_with_retry(self.llm.invoke, prompt)
                return res.content
            async def a_generate(self, prompt: str) -> str:
                res = await self._a_execute_with_retry(self.llm.ainvoke, prompt)
                return res.content
            def get_model_name(self):
                return self.model_name

        custom_model = GroqLlama()
        test_case = LLMTestCase(
            input=query,
            actual_output=answer,
            retrieval_context=context
        )
        
        relevancy_metric = AnswerRelevancyMetric(threshold=0.5, model=custom_model)
        faithfulness_metric = FaithfulnessMetric(threshold=0.5, model=custom_model)
        
        relevancy_metric.measure(test_case)
        faithfulness_metric.measure(test_case)
        
        return {
            "deepeval_relevancy": relevancy_metric.score,
            "deepeval_faithfulness": faithfulness_metric.score
        }
    except ImportError as e:
        return {"deepeval_relevancy": 0.0, "deepeval_faithfulness": 0.0, "error": f"Import error: {str(e)}"}
    except Exception as e:
        return {"deepeval_relevancy": 0.0, "deepeval_faithfulness": 0.0, "error": str(e)}
