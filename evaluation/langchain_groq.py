import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from eval_utils import EVALUATOR_MODEL, call_evaluator_model


class _Res:
    def __init__(self, content):
        self.content = content


class ChatGroq:
    """OpenRouter-backed compatibility shim for evaluator libraries."""

    def __init__(self, model=None, temperature=0, api_key=None, max_retries=3):
        self.model = model if model and "/" in model else EVALUATOR_MODEL
        self.temperature = temperature
        self.max_retries = max_retries

    def _call_sync(self, prompt: str) -> str:
        lower = prompt.lower()
        wants_json = "json" in lower or "schema" in lower
        return call_evaluator_model(prompt, json_mode=wants_json, model=self.model)

    def invoke(self, prompt: str):
        return _Res(self._call_sync(prompt))

    async def ainvoke(self, prompt: str):
        import asyncio
        return await asyncio.to_thread(self.invoke, prompt)

    class _Gen:
        def __init__(self, text: str):
            self.text = text
            self.generation_info = {}

    class _LLMResult:
        def __init__(self, generations):
            self.generations = generations
            self.llm_output = {}

        def flatten(self):
            return [ChatGroq._LLMResult([generation]) for generation in self.generations]

    def generate_prompt(self, prompts, **kwargs):
        texts = [prompt.to_string() if hasattr(prompt, "to_string") else str(prompt) for prompt in prompts]
        return self._LLMResult([[self._Gen(self._call_sync(text))] for text in texts])

    async def agenerate_prompt(self, prompts, **kwargs):
        import asyncio
        return await asyncio.to_thread(self.generate_prompt, prompts, **kwargs)

    def generate(self, prompts, **kwargs):
        return self.generate_prompt(prompts, **kwargs)

    async def agenerate(self, prompts, **kwargs):
        return await self.agenerate_prompt(prompts, **kwargs)
