"""Model provider abstraction."""

import os
import httpx
import re
from groq import AsyncGroq
from google import genai
from config import get_settings

class ModelError(Exception):
    """Base model error."""
    pass

class RequiresPro(ModelError):
    """Raised when a model requires pro/gated access."""
    pass

class ModelUnavailable(ModelError):
    """Raised when a model is not configured or fails."""
    pass

class ModelProvider:
    """Singleton for managing model clients."""

    _instance = None
    
    def __init__(self):
        self.settings = get_settings()
        self.gemini_client = None
        self.groq_client = None
        self.gemini_configured = False
        

        if self.settings.gemini_api_key:
             try:
                self.gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
                self.gemini_configured = True
             except Exception as e:
                print(f"Gemini init failed: {e}")

        if self.settings.groq_api_key:
            try:
                self.groq_client = AsyncGroq(api_key=self.settings.groq_api_key)
            except Exception as e:
                print(f"Groq init failed: {e}")
        

        self.hf_token = os.getenv("HF_TOKEN")
        self.http_client = httpx.AsyncClient(timeout=15.0)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def close(self):
        """Close clients."""
        await self.http_client.aclose()

    
    async def initialize(self):
        """Startup initialization and validation."""
        pass
            
    async def generate_text(self, model_type: str, prompt: str, **kwargs) -> str:
        """Complete text using specified model."""
        if model_type == "gemini":
            result = await self._call_gemini_direct(prompt, **kwargs)
            return result["content"]
        else:
            result = await self.route_inference(prompt, **kwargs)
            return result["content"]

    async def route_inference(self, prompt: str, image_data=None, task="general", **kwargs) -> dict:
        """
        Intelligent Router for Zero-Cost Inference
        """
        
        # 1. VISUAL / HEAVY CONTEXT PATH (Gemini)
        if image_data or len(prompt) > 20000:
            if not self.gemini_configured:
                 raise ModelUnavailable("Gemini is not configured for heavy/visual tasks.")
            
            model_name = "gemini-2.0-flash"

            try:
                contents = [prompt]
                if image_data:
                    contents.append(image_data)
                    
                # Using a timeout if supported by the client, or external wrap
                response = await self.gemini_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config={"http_options": {"timeout": 30000}} # 30s for genai client
                )
                return {"provider": "google", "model": model_name, "content": response.text}
            except Exception as e:
                 print(f"Gemini Heavy Failed: {e}")
                 raise ModelError(f"Gemini generation failed: {e}")

        # 2. NICHE CLASSIFICATION PATH (Hugging Face via HTTPX)
        if task == "classification" and self.hf_token:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api-inference.huggingface.co/models/distilbert-base-uncased-finetuned-sst-2-english",
                        headers={"Authorization": f"Bearer {self.hf_token}"},
                        json={"inputs": prompt},
                        timeout=5.0
                    )
                    response.raise_for_status()
                    return {"provider": "hf", "model": "distilbert", "content": str(response.json())}
            except Exception as e:
                print(f"HF Error: {e}") 
                pass 

        # 3. INTELLIGENT ROUTING (Groq)
        target_model = "llama-3.1-8b-instant" # Default for speed/simple tasks
        max_tokens = 1024
        
        # Mode/Task based routing
        mode = kwargs.get("mode", "").lower()
        
        # A. Coding / Technical / Technical Depth
        if mode == "technical_depth":
            # Return Gemini directly for best quality/depth as requested
             if self.gemini_configured:
                 return await self._call_gemini_direct(prompt, **kwargs)
             target_model = "llama-3.3-70b-versatile"
             max_tokens = 3000
             
        elif task == "coding" or "code" in task.lower():
            target_model = "llama-3.3-70b-versatile"
            max_tokens = 2048
            
        # C. Multilingual Support
        elif kwargs.get("multilingual", False) or any(ord(c) > 127 for c in prompt[:100]):
             target_model = "moonshotai/kimi-k2-instruct-0905"
             max_tokens = 2048
             
        # D. Fast Mode (Llama models only as requested)
        elif mode == "fast":
            # Using llama-3.1-8b-instant for speed
            target_model = "llama-3.1-8b-instant"
            max_tokens = 1200  # Increased from 400 to prevent truncation 
            
        # E. Simple / Old Modes (Explicit Cap)
        elif mode in ["eli5", "eli10"]:
            target_model = "llama-3.1-8b-instant"
            max_tokens = 1200  # Increased from 400 to prevent truncation
            
        # 4. OVERRIDE if specific valid model requested
        req_model = kwargs.get("model")
        if req_model:
            if req_model == "gemini":
                return await self._call_gemini_direct(prompt, **kwargs)
            elif req_model in [
                "llama-3.1-8b-instant", 
                "llama-3.3-70b-versatile", 
                "llama-3.1-70b-versatile", 
                "deepseek-r1-distill-llama-70b", 
                "mixtral-8x7b-32768", 
                "gemma2-9b-it",
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-guard-4-12b"
            ]:
                target_model = req_model

        # 5. EXECUTION
        if not self.groq_client:
             return await self._fallback_chain(prompt)

        is_pro = kwargs.get("is_pro", False) or kwargs.get("premium", False)
        # Apply word cap for fast modes even if pro (user's request)
        if mode == "fast":
             max_tokens = 1200  # Increased from 400 to prevent truncation

        try:
            completion = await self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=target_model,
                max_tokens=max_tokens,
                temperature=kwargs.get("temperature", 0.7),
                timeout=30.0
            )

            content = completion.choices[0].message.content
            # Strict cleaning of CoT and thinking artifacts
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
            content = re.sub(r'Thought:.*?\n\n', '', content, flags=re.DOTALL) # Remove common CoT headers
            content = content.replace("<think>", "").replace("</think>", "").strip()
            
            return {"provider": "groq", "model": target_model, "content": content}
        
        except Exception as e:
            print(f"Groq Error ({target_model}): {e}. Initiating Fallback Chain.")
            return await self._fallback_chain(prompt)

    async def route_inference_stream(self, prompt: str, **kwargs):
        """Stream inference results for real-time UI."""
        mode = kwargs.get("mode", "").lower()

        # Handle Technical Depth streaming
        if mode == "technical_depth" and self.gemini_configured:
            # Revert to Gemini for technical depth as requested
            try:
                # Use a specific stream implementation if needed, but for now we follow the existing pattern
                # If we want literal Gemini streaming, we'd need to add it to genai client
                # However, the user also mentioned "gpt oss to reduce latency"
                target_model = "openai/gpt-oss-120b"
                max_tokens = 3000
            except Exception:
                target_model = "llama-3.3-70b-versatile"
                max_tokens = 3000
        elif mode == "fast":
             # Fast mode uses llama models only
             target_model = "llama-3.1-8b-instant"
             max_tokens = 1200  # Increased from 400 to prevent truncation
        else:
            target_model = "llama-3.1-8b-instant"
            max_tokens = 1024
            
        if mode in ["eli5", "eli10"]:
            max_tokens = 1200  # Increased from 400 to prevent truncation

        if not self.groq_client:
            # Fallback for now just returns full text as a single chunk if streaming is unavailable
            res = await self._fallback_chain(prompt)
            yield res["content"]
            return

        try:
            stream = await self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=target_model,
                max_tokens=max_tokens,
                temperature=kwargs.get("temperature", 0.7),
                stream=True,
                timeout=30.0
            )

            is_thinking = False
            finish_reason = None
            async for chunk in stream:
                content = chunk.choices[0].delta.content
                
                # Capture finish_reason to detect truncation
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                
                if not content:
                    continue
                
                # Enhanced filtering for <think> blocks and CoT headers
                if "<think>" in content:
                    is_thinking = True
                    content = content.split("<think>")[0]
                
                if "Thought:" in content: # Some models use plain text CoT
                    is_thinking = True
                    content = content.split("Thought:")[0]

                if "</think>" in content:
                    is_thinking = False
                    content = content.split("</think>")[-1]
                
                if not is_thinking and content:
                    yield content
            
            # Log if response was truncated and signal frontend
            if finish_reason == 'length':
                print(f"WARNING: Response truncated due to max_tokens limit. Mode: {mode}, Model: {target_model}")
                # Yield special truncation marker for frontend
                yield "\n\n__TRUNCATED__"
        
        except Exception as e:
            print(f"Groq Streaming Error: {e}")
            res = await self._fallback_chain(prompt)
            yield res["content"]

    async def _fallback_chain(self, prompt: str) -> dict:
        """
        Fallback Strategy:
        1. Phi-3 (HF Serverless via HTTPX)
        2. Gemini Flash Lite (Google)
        """
        if self.hf_token:
            try:
                response = await self.http_client.post(
                    "https://api-inference.huggingface.co/models/microsoft/Phi-3-mini-4k-instruct",
                    headers={"Authorization": f"Bearer {self.hf_token}"},
                    json={
                        "inputs": f"<|user|>\n{prompt}<|end|>\n<|assistant|>",
                        "parameters": {"max_new_tokens": 1024, "return_full_text": False}
                    }
                )
                response.raise_for_status()
                result_json = response.json()
                # HF Inference API returns list of dicts with 'generated_text'
                content = result_json[0].get('generated_text', '') if isinstance(result_json, list) else str(result_json)
                return {"provider": "hf-fallback", "model": "phi-3", "content": content}
            except Exception as e:
                print(f"Phi-3 Fallback Failed: {repr(e)}")

        return await self._fallback_to_gemini(prompt)

    async def _fallback_to_gemini(self, prompt: str) -> dict:
        if not self.gemini_configured:
            raise ModelUnavailable("All providers (Groq, HF, Gemini) failed or configured incorrectly.")
        
        try:
            model_name = "gemini-2.0-flash"
            response = await self.gemini_client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"http_options": {"timeout": 30000}}
            )
            return {
                "provider": "google-fallback", 
                "model": model_name,
                "content": response.text
            }
        except Exception as e:
             raise ModelError(f"Critical: Fallback Gemini generation failed: {str(e)}")

    async def _call_gemini_direct(self, prompt: str, **kwargs) -> dict:
        """Legacy direct call."""
        if not self.gemini_configured:
            raise ModelUnavailable("Gemini is not configured.")
        
        try:
            response = await self.gemini_client.aio.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt,
                config={"http_options": {"timeout": 30000}}
            )
            return {"provider": "google", "model": "gemini-2.0-flash", "content": response.text}
        except Exception as e:
            raise ModelError(f"Gemini generation failed: {str(e)}")
