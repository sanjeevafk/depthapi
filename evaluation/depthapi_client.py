import asyncio
import httpx
from typing import Dict, Any, Optional

class DepthAPIClient:
    """Async client for interacting with DepthAPI."""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        import os
        base_url = os.getenv("DEPTHAPI_BASE_URL", base_url)
        self.base_url = base_url.rstrip("/")
        dev_key = os.getenv("DEV_API_KEYS", "sk-depth-test-key-12345").split(",")[0].strip()
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {dev_key}"}
        )
        # Support a mock mode to avoid needing a running DepthAPI server.
        # Set MOCK_DEPTHAPI=1 in the environment to enable.
        self._mock = os.getenv("MOCK_DEPTHAPI", "0") == "1"

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def query(self, query: str, prompt_spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Query DepthAPI with an optional PromptSpec.
        """
        mapped_spec = {
            "depth": "accessible",
            "task": "explain",
            "reasoning": "direct",
            "style": "normal",
            "capabilities": []
        }

        if prompt_spec:
            depth_map = {
                "surface": "simple",
                "detailed": "accessible",
                "expert": "expert",
                "academic": "technical"
            }
            if "depth" in prompt_spec:
                mapped_spec["depth"] = depth_map.get(prompt_spec["depth"], "accessible")

            tone_map = {
                "objective": "direct",
                "educational": "socratic",
                "critical": "debate",
                "concise": "guided"
            }
            if "tone" in prompt_spec:
                mapped_spec["reasoning"] = tone_map.get(prompt_spec["tone"], "direct")

            format_map = {
                "markdown": "normal",
                "bullet_points": "concise",
                "essay": "academic",
                "code_heavy": "normal"
            }
            if "format" in prompt_spec:
                mapped_spec["style"] = format_map.get(prompt_spec["format"], "normal")

            if prompt_spec.get("include_citations"):
                mapped_spec["capabilities"].append("requires_citations")

        payload = {
            "topic": query,
            "prompt_spec": mapped_spec,
            "mode": "chat",
            "bypass_cache": True,
        }

        # Mocked response path
        if self._mock:
            # produce a deterministic short answer and fake context
            answer = f"(MOCK) Explanation for: {query}"
            contexts = [{"text": f"Mock context paragraph about {query}"}]
            return {"answer": answer, "contexts": contexts, "citations": [], "metadata": {}, "error": None}

        try:
            response = await self.client.post(
                f"{self.base_url}/api/query",
                json=payload
            )
            response.raise_for_status()
            res_json = response.json()
            explanations = res_json.get("explanations", {})
            answer = next(iter(explanations.values()), "") if explanations else ""
            contexts = res_json.get("contexts") or []
            return {
                "answer": answer,
                "contexts": contexts,
                "citations": res_json.get("citations") or [],
                "metadata": res_json.get("metadata") or {},
                "error": None,
            }
        except httpx.HTTPError as e:
            detail = ""
            try:
                detail = f": {response.json()}"
            except Exception:
                pass
            return {"error": f"{str(e)}{detail}", "answer": "Error fetching from API", "contexts": [], "citations": [], "metadata": {}}
