import os

import pytest

from api.config import get_settings
from api.services.inference.llm_client import close_llm_client, create_chat_completion


@pytest.mark.asyncio
async def test_real_provider_client_sampled_smoke():
    if os.getenv("RUN_REAL_PROVIDER_TESTS", "").strip() != "1":
        pytest.skip("Set RUN_REAL_PROVIDER_TESTS=1 to run real-provider sample.")

    settings = get_settings()
    has_any_provider_key = any(
        bool(getattr(settings, attr, "") or "")
        for attr in ("groq_api_key", "cerebras_api_key", "gemini_api_key", "openrouter_api_key")
    )
    if not has_any_provider_key:
        pytest.skip("No provider API keys configured for real-provider sample test.")

    response = await create_chat_completion(
        model="default-fast",
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly: DEPTHAPI_PROVIDER_OK",
            }
        ],
        max_tokens=300,
        temperature=0,
    )

    text = str(getattr(response.choices[0].message, "content", "") or "").strip().upper()
    try:
        assert "DEPTHAPI_PROVIDER_OK" in text
    finally:
        await close_llm_client()
