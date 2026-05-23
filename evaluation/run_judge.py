import os
import asyncio
from typing import Dict, Any, List
import json

try:
    from groq import AsyncGroq
except ImportError:
    pass # Handle missing Groq client gracefully in script

class CustomLLMJudge:
    """LLM-as-a-judge using Groq Llama 3.1 70B."""
    
    def __init__(self):
        # Prefer Gemini (Google Generative API) when a GEMINI_API_KEY or
        # GOOGLE_APPLICATION_CREDENTIALS is available; otherwise fall back
        # to Groq if configured.
        self.client = None
        self.model = "llama-3.3-70b-versatile"

        gemini_key = os.environ.get("GEMINI_API_KEY")
        gcloud_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

        self.use_gemini = False
        if gemini_key or gcloud_creds:
            try:
                import google.generativeai as genai
                # Configure with explicit API key if present
                if gemini_key:
                    genai.configure(api_key=gemini_key)
                # Only enable Gemini judge if the chat.create API is present
                has_chat = hasattr(genai, "chat") and hasattr(genai.chat, "create")
                if has_chat:
                    self.genai = genai
                    self.use_gemini = True
                    self.model = os.environ.get("GEMINI_MODEL", "gemini-1.0")
                    print(f"CustomLLMJudge: configured to use Gemini model {self.model}")
                else:
                    print("Gemini client available but chat.create API not found; will fall back to Groq if configured.")
            except Exception as e:
                print(f"Gemini client unavailable: {e}. Falling back to Groq if configured.")

        if not self.use_gemini:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                print("Warning: GROQ_API_KEY not set. Judge will return empty scores.")
                self.client = None
            else:
                try:
                    self.client = AsyncGroq(api_key=api_key)
                except Exception as e:
                    print(f"Failed to initialize Groq client: {e}")
                    self.client = None
        
    async def evaluate(self, query: str, answer: str, context: List[str], prompt_spec: Dict[str, Any] = None) -> Dict[str, Any]:
        """Evaluate the answer based on multiple criteria."""
        # Prepare prompt and helpers
        context_str = "\\n\\n".join(context)
        spec_str = json.dumps(prompt_spec) if prompt_spec else "None"
        prompt = f"""You are an expert evaluator. Evaluate the following RAG system response.

Query: {query}
Prompt Spec: {spec_str}
Context: {context_str}
Answer: {answer}

Evaluate on a scale of 1-5 for:
1. Depth Compliance: Does the answer match the requested depth and tone in the Prompt Spec?
2. Answer Quality: Is the answer well-structured, clear, and accurate?
3. Citation Accuracy: Does it properly cite sources if requested?
4. Faithfulness: Is the answer entirely based on the provided Context?

Return your evaluation as a JSON object strictly following this format:
{{
    "depth_compliance": <int>,
    "answer_quality": <int>,
    "citation_accuracy": <int>,
    "faithfulness": <int>,
    "reasoning": "<brief reasoning for scores>"
}}
"""

        retries = 3
        delay = 2.0

        # If Gemini is configured, use it first
        if self.use_gemini:
            try:
                # Prefer chat API if available
                if hasattr(self.genai, "chat") and hasattr(self.genai.chat, "create"):
                    for attempt in range(retries):
                        try:
                            resp = self.genai.chat.create(model=self.model, text=prompt)
                            content = None
                            if hasattr(resp, "candidates") and resp.candidates:
                                content = resp.candidates[0].content
                            elif hasattr(resp, "output"):
                                content = resp.output
                            elif isinstance(resp, dict):
                                content = resp.get("candidates", [{}])[0].get("content") or resp.get("output")
                            if not content:
                                raise ValueError("Empty response from Gemini chat API")
                            try:
                                return json.loads(content)
                            except Exception:
                                import re
                                m = re.search(r"\{.*\}", content, flags=re.S)
                                if m:
                                    return json.loads(m.group(0))
                                raise
                        except Exception as e:
                            if attempt == retries - 1:
                                return {"depth_compliance": 0, "answer_quality": 0, "citation_accuracy": 0, "faithfulness": 0, "reasoning": str(e)}
                            await asyncio.sleep(delay * (2 ** attempt))
                # If chat.create isn't available we don't attempt other Gemini APIs here
                # and will fall back to Groq (or return an informative error later).
            except Exception as e:
                return {"depth_compliance": 0, "answer_quality": 0, "citation_accuracy": 0, "faithfulness": 0, "reasoning": str(e)}

        # Otherwise fall back to Groq client if available
        if not self.client:
            return {"depth_compliance": 0, "answer_quality": 0, "citation_accuracy": 0, "faithfulness": 0, "reasoning": "No judge client configured"}

        for attempt in range(retries):
            try:
                response = await self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.model,
                    response_format={"type": "json_object"}
                )
                result = json.loads(response.choices[0].message.content)
                return result
            except Exception as e:
                if attempt == retries - 1:
                    print(f"Judge Error: {e}")
                    return {"depth_compliance": 0, "answer_quality": 0, "citation_accuracy": 0, "faithfulness": 0, "reasoning": str(e)}
                sleep_time = delay * (2 ** attempt)
                await asyncio.sleep(sleep_time)
