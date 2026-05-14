
import asyncio
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.getcwd())

# Mock modules that might be missing in the environment but are imported at the top level
sys.modules["api.services.rag_backend_router"] = MagicMock()
sys.modules["api.services.search"] = MagicMock()
sys.modules["api.services.inference_search"] = MagicMock()
sys.modules["faiss"] = MagicMock()
sys.modules["supabase"] = MagicMock()
sys.modules["numpy"] = MagicMock()
sys.modules["filelock"] = MagicMock()
sys.modules["rank_bm25"] = MagicMock()

# Mock environment variables
os.environ["GROQ_API_KEY"] = "mock_key"
os.environ["GEMINI_API_KEY"] = "mock_key"
os.environ["REDIS_URL"] = "redis://localhost:6379"

from api.services.inference.inference import generate_explanation
from api.utils import FREE_LEVELS

async def run_depth_tests(topic: str):
    print(f"[START] Running Depth Verification for topic: '{topic}'\n")
    print("-" * 80)
    
    # Patching the correct references inside inference.py
    with patch("api.services.inference.create_chat_completion", new_callable=AsyncMock) as mock_completion:
        with patch("api.services.inference.call_with_quality_escalation", new_callable=AsyncMock) as mock_escalation:
            with patch("api.services.inference.retrieve_rag_context", new_callable=AsyncMock) as mock_rag:
                with patch("api.services.inference.search_service.load_search_context", new_callable=AsyncMock) as mock_search:
                    
                    mock_rag.return_value = []
                    mock_search.return_value = ""
                    
                    for level in FREE_LEVELS:
                        # Prepare fake responses
                        mock_content = f"[MOCK {level.upper()}] Explanation of {topic}."
                        
                        # Return content from escalation mock (which is used in 'learn' mode)
                        mock_escalation.return_value = mock_content
                        
                        # Prepare a fake completion result for anything else
                        mock_res = MagicMock()
                        mock_res.choices = [MagicMock()]
                        mock_res.choices[0].message.content = mock_content
                        mock_res.usage = {"total_tokens": 100}
                        mock_completion.return_value = mock_res
                        
                        try:
                            # Trigger the pipeline
                            response = await generate_explanation(topic, level, mode="technical")
                            
                            # Capture what was decided
                            # Note: generate_explanation calls call_with_quality_escalation for the final answer
                            call_args = mock_escalation.call_args
                            routed_aliases = call_args.args[0] if call_args.args else call_args.kwargs.get("aliases")
                            prompt = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("prompt")
                            
                            print(f"[PASS] DEPTH: {level.upper()}")
                            print(f"   Model Aliases: {routed_aliases}")
                            print(f"   System Prompt (start): {prompt[:100]}...")
                            print(f"   Output: {response}\n")
                            
                        except Exception as e:
                            print(f"[FAIL] DEPTH: {level.upper()} failed: {str(e)}")

    print("-" * 80)
    print("[END] Verification Complete.")

if __name__ == "__main__":
    # Complex technical query designed to trigger escalation to Gemini Pro
    complex_topic = "Derive the mathematical proof for the Heisenberg Uncertainty Principle using the non-commutation of position and momentum operators."
    asyncio.run(run_depth_tests(complex_topic))
