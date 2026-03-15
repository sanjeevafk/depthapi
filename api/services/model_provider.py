"""Model provider abstraction and fallback routing."""

import asyncio
import os
import re
import time
from typing import Any, Optional

import httpx
from google import genai
from groq import AsyncGroq

from config import get_settings
from logging_config import logger
from utils import LEARNING_MODE, TECHNICAL_MODE, normalize_mode

GROQ_PROVIDER = "groq"
GEMINI_PROVIDER = "gemini"
OPENROUTER_PROVIDER = "openrouter"
HUGGINGFACE_PROVIDER = "huggingface"

NORMAL_PROVIDER_ORDER = (
    GEMINI_PROVIDER,
    GROQ_PROVIDER,
    OPENROUTER_PROVIDER,
    HUGGINGFACE_PROVIDER,
)
LEARNING_PROVIDER_ORDER = NORMAL_PROVIDER_ORDER
TECHNICAL_PROVIDER_ORDER = (
    GEMINI_PROVIDER,
    HUGGINGFACE_PROVIDER,
)

PROVIDER_COOLDOWN_SECONDS = 60  # Start with 1 minute, consider exponential backoff
GEMINI_PRIMARY_MODEL = "gemini-2.5-pro"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_ALLOWLIST = {
    GEMINI_PRIMARY_MODEL,
    GEMINI_FALLBACK_MODEL,
}
OPENROUTER_PRIMARY_MODEL = "qwen/qwen3.5-9b"
OPENROUTER_FALLBACK_MODEL = "anthropic/claude-sonnet-4.6"
OPENROUTER_MODEL_ALIAS = {
    "qwen-9b": OPENROUTER_PRIMARY_MODEL,
    "sonnet-4.6": OPENROUTER_FALLBACK_MODEL,
}
OPENROUTER_MODEL_ALLOWLIST = {
    OPENROUTER_PRIMARY_MODEL,
    OPENROUTER_FALLBACK_MODEL,
    *OPENROUTER_MODEL_ALIAS.keys(),
}
HUGGINGFACE_FALLBACK_MODEL = "deepseek-ai/DeepSeek-R1"
HUGGINGFACE_SECONDARY_MODEL = "microsoft/phi-4"
HUGGINGFACE_JUDGE_MODEL = "MiniMaxAI/MiniMax-M2.5"
HUGGINGFACE_MODEL_ALIAS = {
    "deepseek": HUGGINGFACE_FALLBACK_MODEL,
    "phi-4": HUGGINGFACE_SECONDARY_MODEL,
}
HUGGINGFACE_MODEL_ALLOWLIST = {
    HUGGINGFACE_FALLBACK_MODEL,
    HUGGINGFACE_SECONDARY_MODEL,
    HUGGINGFACE_JUDGE_MODEL,
    *HUGGINGFACE_MODEL_ALIAS.keys(),
}
MODEL_CHAT_TEMPLATES = {
    "microsoft/phi-4": "<|user|>\n{prompt}<|end|>\n<|assistant|>",
    "deepseek-ai/deepseek-r1": "User: {prompt}\nAssistant:",
    "minimaxai/minimax-m2.5": "<User>{prompt}</User><Assistant>",
}

GROQ_LLAMA_8B_MODEL = "llama-3.1-8b-instant"
GROQ_MODEL_ALIAS = {
    "llama-8b": GROQ_LLAMA_8B_MODEL,
}
GROQ_MODEL_ALLOWLIST = {
    GROQ_LLAMA_8B_MODEL,
    "llama-8b",
    "openai/gpt-oss-20b",
}
RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "429",
    "resource_exhausted",
    "rate_limit_exceeded",
    "quota exceeded",
    "quota_exceeded",
)


class ModelError(Exception):
    """Base model error."""


class RequiresPro(ModelError):
    """Raised when a model requires pro/gated access."""


class ModelUnavailable(ModelError):
    """Raised when a model is not configured or fails."""


class ModelProvider:
    """Singleton for managing model clients and fallback routing."""

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
            except Exception as exc:
                logger.warning("gemini_init_failed", error=str(exc))

        if self.settings.groq_api_key:
            try:
                self.groq_client = AsyncGroq(api_key=self.settings.groq_api_key)
            except Exception as exc:
                logger.warning("groq_init_failed", error=str(exc))

        self.openrouter_api_key = (
            getattr(self.settings, "openrouter_api_key", "") or os.getenv("OPENROUTER_API_KEY", "")
        )
        self.openrouter_primary_model = self._validated_model(
            getattr(self.settings, "openrouter_model", "") or os.getenv("OPENROUTER_MODEL", OPENROUTER_PRIMARY_MODEL),
            OPENROUTER_MODEL_ALLOWLIST,
            OPENROUTER_PRIMARY_MODEL,
            alias_map=OPENROUTER_MODEL_ALIAS,
        )
        self.openrouter_fallback_model = self._validated_model(
            getattr(self.settings, "openrouter_fallback_model", "")
            or os.getenv("OPENROUTER_FALLBACK_MODEL", OPENROUTER_FALLBACK_MODEL),
            OPENROUTER_MODEL_ALLOWLIST,
            OPENROUTER_FALLBACK_MODEL,
            alias_map=OPENROUTER_MODEL_ALIAS,
        )
        self.gemini_primary_model = self._validated_model(
            getattr(self.settings, "gemini_primary_model", "") or GEMINI_PRIMARY_MODEL,
            GEMINI_MODEL_ALLOWLIST,
            GEMINI_PRIMARY_MODEL,
        )
        self.gemini_fallback_model = self._validated_model(
            getattr(self.settings, "gemini_fallback_model", "") or GEMINI_FALLBACK_MODEL,
            GEMINI_MODEL_ALLOWLIST,
            GEMINI_FALLBACK_MODEL,
        )
        self.hf_token = os.getenv("HF_TOKEN")
        self.hf_primary_model = self._validated_model(
            getattr(self.settings, "huggingface_fallback_model", "")
            or os.getenv("HUGGINGFACE_FALLBACK_MODEL", HUGGINGFACE_FALLBACK_MODEL),
            HUGGINGFACE_MODEL_ALLOWLIST,
            HUGGINGFACE_FALLBACK_MODEL,
            alias_map=HUGGINGFACE_MODEL_ALIAS,
        )
        self.hf_secondary_model = self._validated_model(
            getattr(self.settings, "huggingface_secondary_model", "")
            or os.getenv("HUGGINGFACE_SECONDARY_MODEL", HUGGINGFACE_SECONDARY_MODEL),
            HUGGINGFACE_MODEL_ALLOWLIST,
            HUGGINGFACE_SECONDARY_MODEL,
            alias_map=HUGGINGFACE_MODEL_ALIAS,
        )
        self.hf_fallback_models = list(
            dict.fromkeys([model for model in (self.hf_primary_model, self.hf_secondary_model) if model])
        )
        self.hf_fallback_model = self.hf_primary_model
        self.hf_judge_model = self._validated_model(
            getattr(self.settings, "huggingface_judge_model", "")
            or os.getenv("HUGGINGFACE_JUDGE_MODEL", HUGGINGFACE_JUDGE_MODEL),
            HUGGINGFACE_MODEL_ALLOWLIST,
            HUGGINGFACE_JUDGE_MODEL,
            alias_map=HUGGINGFACE_MODEL_ALIAS,
        )
        self.http_client = httpx.AsyncClient(timeout=60.0)
        self.provider_status: dict[str, dict[str, Optional[float]]] = {
            GROQ_PROVIDER: {"blockedUntil": None},
            GEMINI_PROVIDER: {"blockedUntil": None},
            OPENROUTER_PROVIDER: {"blockedUntil": None},
            HUGGINGFACE_PROVIDER: {"blockedUntil": None},
        }

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
        return None

    async def generate_text(self, model_type: str, prompt: str, **kwargs) -> str:
        """Complete text using specified model."""
        result = await self.route_inference(prompt, model=model_type, **kwargs)
        return result["content"]

    async def route_inference(self, prompt: str, image_data=None, task="general", **kwargs) -> dict:
        """Select a provider based on mode, health, and fallback availability."""
        provider_chain = self._resolve_provider_chain(
            mode=kwargs.get("mode", ""),
            model=kwargs.get("model"),
            image_data=image_data,
            prompt=prompt,
        )
        return await self._fallback_chain(
            prompt,
            providers=provider_chain,
            image_data=image_data,
            task=task,
            **kwargs,
        )

    async def route_inference_stream(self, prompt: str, image_data=None, task="general", **kwargs):
        """Stream inference results when Groq is the selected provider, else fall back to full-text."""
        provider_chain = self._resolve_provider_chain(
            mode=kwargs.get("mode", ""),
            model=kwargs.get("model"),
            image_data=image_data,
            prompt=prompt,
        )
        if not provider_chain:
            raise ModelUnavailable("No configured providers are currently available.")

        primary_provider = provider_chain[0]
        if primary_provider != GROQ_PROVIDER or image_data:
            result = await self._fallback_chain(
                prompt,
                providers=provider_chain,
                image_data=image_data,
                task=task,
                **kwargs,
            )
            yield result["content"]
            return

        target_model, max_tokens = self._resolve_groq_model(prompt, task=task, **kwargs)
        logger.info(
            "[LLM Router] Provider selected",
            provider=GROQ_PROVIDER,
            route_mode=self._route_label(kwargs.get("mode", ""), image_data, prompt),
            model=target_model,
            streaming=True,
        )

        try:
            if not self.groq_client:
                raise ModelUnavailable("Groq is not configured.")
            stream = await self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=target_model,
                max_tokens=max_tokens,
                temperature=kwargs.get("temperature", 0.7),
                stream=True,
                timeout=30.0,
            )

            thinking_mode: str | None = None
            finish_reason = None
            pending_fragment = ""
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                content = choice.delta.content
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                if not content:
                    continue
                visible_content, thinking_mode, pending_fragment = self._filter_stream_chunk(
                    pending_fragment + content,
                    thinking_mode,
                )
                if visible_content:
                    yield visible_content

            if finish_reason == "length":
                logger.warning(
                    "groq_stream_truncated",
                    mode=kwargs.get("mode", ""),
                    model=target_model,
                )
                yield "\n\n__TRUNCATED__"
        except Exception as exc:
            self._log_provider_failure(GROQ_PROVIDER, exc)
            fallback_result = await self._fallback_chain(
                prompt,
                providers=provider_chain[1:],
                image_data=image_data,
                task=task,
                **kwargs,
            )
            yield fallback_result["content"]

    async def _fallback_chain(self, prompt: str, providers=None, image_data=None, task="general", **kwargs) -> dict:
        """Try providers in order until one succeeds."""
        chain = providers
        if chain is None:
            chain = self._resolve_provider_chain(
                mode=kwargs.get("mode", ""),
                model=kwargs.get("model"),
                image_data=image_data,
                prompt=prompt,
            )
        if not chain:
            raise ModelUnavailable("No configured providers are currently available.")

        last_error = None
        for index, provider_name in enumerate(chain):
            logger.info(
                "[LLM Router] Provider selected",
                provider=provider_name,
                route_mode=self._route_label(kwargs.get("mode", ""), image_data, prompt),
                attempt=index + 1,
                total_candidates=len(chain),
            )
            try:
                return await self._call_provider(
                    provider_name,
                    prompt,
                    image_data=image_data,
                    task=task,
                    **kwargs,
                )
            except Exception as exc:
                last_error = exc
                self._log_provider_failure(provider_name, exc)
                if index + 1 < len(chain):
                    logger.info(
                        "[LLM Router] Switching provider",
                        from_provider=provider_name,
                        to_provider=chain[index + 1],
                    )

        if isinstance(last_error, ModelUnavailable):
            raise last_error
        if isinstance(last_error, ModelError):
            raise last_error
        raise ModelError(f"All providers failed: {last_error}")

    async def _call_provider(self, provider_name: str, prompt: str, image_data=None, task="general", **kwargs) -> dict:
        if provider_name == GROQ_PROVIDER:
            return await self._call_groq(prompt, task=task, **kwargs)
        if provider_name == GEMINI_PROVIDER:
            return await self._call_gemini_direct(prompt, image_data=image_data, **kwargs)
        if provider_name == OPENROUTER_PROVIDER:
            return await self._call_openrouter(prompt, task=task, **kwargs)
        if provider_name == HUGGINGFACE_PROVIDER:
            return await self._call_huggingface(prompt, **kwargs)
        raise ModelUnavailable(f"Unknown provider: {provider_name}")

    def _resolve_provider_chain(self, mode: str, model: str | None, image_data, prompt: str) -> list[str]:
        if image_data:
            return self._available_providers((GEMINI_PROVIDER,))

        if model in GEMINI_MODEL_ALLOWLIST or model == "gemini":
            base_order = (
                GEMINI_PROVIDER,
                GROQ_PROVIDER,
                OPENROUTER_PROVIDER,
                HUGGINGFACE_PROVIDER,
            )
        elif model in OPENROUTER_MODEL_ALLOWLIST:
            base_order = (
                OPENROUTER_PROVIDER,
                GEMINI_PROVIDER,
                GROQ_PROVIDER,
                HUGGINGFACE_PROVIDER,
            )
        elif model in GROQ_MODEL_ALLOWLIST:
            base_order = (
                GROQ_PROVIDER,
                GEMINI_PROVIDER,
                OPENROUTER_PROVIDER,
                HUGGINGFACE_PROVIDER,
            )
        elif model in HUGGINGFACE_MODEL_ALLOWLIST:
            base_order = (
                HUGGINGFACE_PROVIDER,
                GEMINI_PROVIDER,
                GROQ_PROVIDER,
                OPENROUTER_PROVIDER,
            )
        elif model == "openrouter":
            base_order = (
                OPENROUTER_PROVIDER,
                GEMINI_PROVIDER,
                GROQ_PROVIDER,
                HUGGINGFACE_PROVIDER,
            )
        elif model == "groq":
            base_order = (
                GROQ_PROVIDER,
                GEMINI_PROVIDER,
                OPENROUTER_PROVIDER,
                HUGGINGFACE_PROVIDER,
            )
        elif model == "huggingface":
            base_order = (
                HUGGINGFACE_PROVIDER,
                GEMINI_PROVIDER,
                GROQ_PROVIDER,
                OPENROUTER_PROVIDER,
            )
        elif self._route_label(mode, image_data, prompt) == "technical":
            base_order = TECHNICAL_PROVIDER_ORDER
        else:
            base_order = LEARNING_PROVIDER_ORDER

        return self._available_providers(base_order)

    def _available_providers(self, provider_order) -> list[str]:
        available = []
        now = time.time()
        for provider_name in provider_order:
            if not self._is_provider_configured(provider_name):
                continue

            blocked_until = self.provider_status[provider_name]["blockedUntil"]
            if blocked_until and blocked_until > now:
                logger.info(
                    "[LLM Router] Provider skipped",
                    provider=provider_name,
                    reason="circuit_breaker",
                    blocked_until=blocked_until,
                )
                continue

            if blocked_until and blocked_until <= now:
                self.provider_status[provider_name]["blockedUntil"] = None
                logger.info(
                    "[LLM Router] Circuit breaker recovered",
                    provider=provider_name,
                )

            available.append(provider_name)
        return available

    def _is_provider_configured(self, provider_name: str) -> bool:
        if provider_name == GROQ_PROVIDER:
            return self.groq_client is not None
        if provider_name == GEMINI_PROVIDER:
            return self.gemini_configured
        if provider_name == OPENROUTER_PROVIDER:
            return bool(self.openrouter_api_key)
        if provider_name == HUGGINGFACE_PROVIDER:
            return bool(self.hf_token and self.hf_fallback_models)
        return False

    def _route_label(self, mode: str, image_data, prompt: str) -> str:
        normalized_mode = normalize_mode(mode)
        if image_data or len(prompt) > 20000 or normalized_mode == TECHNICAL_MODE:
            return TECHNICAL_MODE
        return LEARNING_MODE

    def _resolve_groq_model(self, prompt: str, task="general", **kwargs) -> tuple[str, int]:
        target_model = GROQ_LLAMA_8B_MODEL
        max_tokens = 1024
        mode = normalize_mode(kwargs.get("mode"))

        if task == "coding" or "code" in task.lower():
            target_model = "openai/gpt-oss-20b"
            max_tokens = 2048
        elif mode == LEARNING_MODE:
            max_tokens = 1200

        requested_model = kwargs.get("model")
        if requested_model in GROQ_MODEL_ALLOWLIST:
            target_model = GROQ_MODEL_ALIAS.get(requested_model, requested_model)

        # Ensure target_model is always a string
        if target_model is None:
            target_model = GROQ_LLAMA_8B_MODEL

        return target_model, max_tokens

    def _groq_candidate_models(self, task="general", **kwargs) -> list[str]:
        requested_model = kwargs.get("model")
        if requested_model in GROQ_MODEL_ALLOWLIST:
            return [model for model in [GROQ_MODEL_ALIAS.get(requested_model, requested_model)] if model is not None]
        if task == "coding" or "code" in task.lower():
            return ["openai/gpt-oss-20b", GROQ_LLAMA_8B_MODEL]
        return [GROQ_LLAMA_8B_MODEL, "openai/gpt-oss-20b"]

    async def _call_groq(self, prompt: str, task="general", **kwargs) -> dict:
        if not self.groq_client:
            raise ModelUnavailable("Groq is not configured.")

        _, max_tokens = self._resolve_groq_model(prompt, task=task, **kwargs)
        last_error: Exception | None = None
        for target_model in self._groq_candidate_models(task=task, **kwargs):
            try:
                completion = await self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=target_model,
                    max_tokens=max_tokens,
                    temperature=kwargs.get("temperature", 0.7),
                    timeout=30.0,
                )
                content = completion.choices[0].message.content or ""
                return {
                    "provider": GROQ_PROVIDER,
                    "model": target_model,
                    "content": self._clean_text(content),
                }
            except Exception as exc:
                last_error = exc
                logger.warning("groq_model_failed", model=target_model, error=str(exc))

        raise ModelError(f"Groq models failed: {last_error}")

    async def _call_openrouter(self, prompt: str, task="general", **kwargs) -> dict:
        if not self.openrouter_api_key:
            raise ModelUnavailable("OpenRouter is not configured.")

        _, max_tokens = self._resolve_groq_model(prompt, task=task, **kwargs)
        last_error: Exception | None = None
        for target_model in self._openrouter_candidate_models(**kwargs):
            try:
                response = await self.http_client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": target_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": kwargs.get("temperature", 0.7),
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                message = payload.get("choices", [{}])[0].get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                return {
                    "provider": OPENROUTER_PROVIDER,
                    "model": target_model,
                    "content": self._clean_text(str(content)),
                }
            except Exception as exc:
                last_error = exc
                logger.warning("openrouter_model_failed", model=target_model, error=str(exc))

        raise ModelError(f"OpenRouter models failed: {last_error}")

    async def _call_huggingface(self, prompt: str, **kwargs) -> dict:
        if not self.hf_token or not self.hf_fallback_models:
            raise ModelUnavailable("HuggingFace is not configured.")

        requested_model = kwargs.get("model")
        if requested_model in HUGGINGFACE_MODEL_ALIAS:
            requested_model = HUGGINGFACE_MODEL_ALIAS[requested_model]
        if requested_model in HUGGINGFACE_MODEL_ALLOWLIST and requested_model != self.hf_judge_model:
            models_to_try = [requested_model]
        else:
            mode = normalize_mode(kwargs.get("mode"))
            if mode == TECHNICAL_MODE:
                models_to_try = [self.hf_primary_model]
            else:
                models_to_try = list(self.hf_fallback_models)

        last_error: Exception | None = None
        for model_name in models_to_try:
            try:
                content = await self._call_huggingface_model(prompt, model_name)
                return {
                    "provider": HUGGINGFACE_PROVIDER,
                    "model": model_name,
                    "content": self._clean_text(content),
                }
            except Exception as exc:
                last_error = exc
                logger.warning("huggingface_model_failed", model=model_name, error=str(exc))

        raise ModelError(f"HuggingFace models failed: {last_error}")

    async def _call_huggingface_model(self, prompt: str, model_name: str) -> str:
        response = await self.http_client.post(
            f"https://api-inference.huggingface.co/models/{model_name}",
            headers={"Authorization": f"Bearer {self.hf_token}"},
            json={
                "inputs": self._format_hf_prompt(prompt, model_name),
                "parameters": {"max_new_tokens": 1024, "return_full_text": False},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            return payload[0].get("generated_text", "")
        if isinstance(payload, list):
            return ""
        return str(payload)

    def _format_hf_prompt(self, prompt: str, model_name: str) -> str:
        normalized_model_name = model_name.strip().lower()
        template = MODEL_CHAT_TEMPLATES.get(normalized_model_name)
        if template is None:
            for model_prefix, candidate_template in MODEL_CHAT_TEMPLATES.items():
                if normalized_model_name.startswith(model_prefix):
                    template = candidate_template
                    break
        if template is None:
            template = "{prompt}"
        return template.format(prompt=prompt)

    async def judge_candidates(self, prompt: str) -> str:
        if not self.hf_token or not self.hf_judge_model:
            raise ModelUnavailable("HuggingFace judge model is not configured.")

        response = await self.http_client.post(
            f"https://api-inference.huggingface.co/models/{self.hf_judge_model}",
            headers={"Authorization": f"Bearer {self.hf_token}"},
            json={
                "inputs": prompt,
                "parameters": {"max_new_tokens": 1024, "return_full_text": False},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            content = payload[0].get("generated_text", "")
        elif isinstance(payload, list):
            content = ""
        else:
            content = str(payload)
        return self._clean_text(content)

    async def _fallback_to_gemini(self, prompt: str) -> dict:
        if not self.gemini_configured:
            raise ModelUnavailable("Gemini is not configured.")

        try:
            return await self._call_gemini_direct(prompt)
        except Exception as exc:
            raise ModelError(f"Critical: Fallback Gemini generation failed: {exc}") from exc

    async def _call_gemini_direct(self, prompt: str, image_data=None, **kwargs) -> dict:
        """Direct Gemini call for technical mode and heavy-context requests."""
        if not self.gemini_configured or self.gemini_client is None:
            raise ModelUnavailable("Gemini is not configured.")

        target_model = self._resolve_gemini_model(image_data=image_data, **kwargs)
        contents: Any = prompt
        if image_data is not None:
            contents = [prompt, image_data]

        response = await asyncio.to_thread(
            self.gemini_client.models.generate_content,
            model=target_model,
            contents=contents,
            config={"http_options": {"timeout": 30000}},
        )
        return {
            "provider": GEMINI_PROVIDER,
            "model": target_model,
            "content": self._clean_text(response.text or ""),
        }

    def _resolve_gemini_model(self, image_data=None, **kwargs) -> str:
        requested_model = kwargs.get("model")
        if requested_model in GEMINI_MODEL_ALLOWLIST:
            return requested_model
        mode = normalize_mode(kwargs.get("mode"))
        if image_data is not None or mode == TECHNICAL_MODE:
            return self.gemini_primary_model
        return self.gemini_fallback_model

    def _resolve_openrouter_model(self, **kwargs) -> str:
        requested_model = kwargs.get("model")
        if requested_model in OPENROUTER_MODEL_ALLOWLIST:
            resolved = OPENROUTER_MODEL_ALIAS.get(requested_model, requested_model)
            return resolved or self.openrouter_primary_model
        mode = normalize_mode(kwargs.get("mode"))
        if mode == TECHNICAL_MODE:
            return self.openrouter_fallback_model
        return self.openrouter_primary_model

    def _openrouter_candidate_models(self, **kwargs) -> list[str]:
        requested_model = kwargs.get("model")
        if requested_model in OPENROUTER_MODEL_ALLOWLIST:
            resolved = OPENROUTER_MODEL_ALIAS.get(requested_model, requested_model)
            if resolved:
                return [resolved]
            return [self.openrouter_primary_model]
        mode = normalize_mode(kwargs.get("mode"))
        if mode == TECHNICAL_MODE:
            return [self.openrouter_fallback_model]
        return [self.openrouter_primary_model, self.openrouter_fallback_model]

    def _validated_model(
        self,
        configured_model: str,
        allowed_models: set[str],
        fallback_model: str,
        alias_map: dict[str, str] | None = None,
    ) -> str:
        resolved_model = alias_map.get(configured_model, configured_model) if alias_map else configured_model
        if resolved_model in allowed_models:
            return resolved_model
        logger.warning(
            "unsupported_model_config",
            configured_model=configured_model,
            fallback_model=fallback_model,
        )
        return fallback_model

    def _clean_text(self, content: str) -> str:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"Thought:.*?\n\n", "", content, flags=re.DOTALL)
        return content.replace("<think>", "").replace("</think>", "").strip()

    def _filter_stream_chunk(self, content: str, thinking_mode: str | None) -> tuple[str, str | None, str]:
        visible_parts: list[str] = []
        cursor = 0

        while cursor < len(content):
            if thinking_mode == "thought":
                closing_index = content.find("\n\n", cursor)
                if closing_index == -1:
                    pending_fragment = self._pending_stream_fragment(content[cursor:], ("\n\n",))
                    return "".join(visible_parts), "thought", pending_fragment
                cursor = closing_index + len("\n\n")
                thinking_mode = None
                continue

            if thinking_mode == "think":
                closing_index = content.find("</think>", cursor)
                if closing_index == -1:
                    pending_fragment = self._pending_stream_fragment(content[cursor:], ("</think>",))
                    return "".join(visible_parts), "think", pending_fragment
                cursor = closing_index + len("</think>")
                thinking_mode = None
                continue

            next_marker = min(
                (
                    (content.find(marker, cursor), marker)
                    for marker in ("<think>", "Thought:", "</think>")
                    if content.find(marker, cursor) != -1
                ),
                default=None,
            )
            if next_marker is None:
                remainder = content[cursor:]
                pending_fragment = self._pending_stream_fragment(
                    remainder,
                    ("<think>", "Thought:", "</think>"),
                )
                if pending_fragment:
                    visible_parts.append(remainder[: -len(pending_fragment)])
                else:
                    visible_parts.append(remainder)
                return "".join(visible_parts), None, pending_fragment

            marker_index, marker = next_marker
            visible_parts.append(content[cursor:marker_index])
            cursor = marker_index + len(marker)

            if marker == "<think>":
                thinking_mode = "think"
            elif marker == "Thought:":
                thinking_mode = "thought"

        return "".join(visible_parts), thinking_mode, ""

    def _pending_stream_fragment(self, content: str, markers: tuple[str, ...]) -> str:
        max_fragment_length = min(len(content), max(len(marker) for marker in markers) - 1)
        for fragment_length in range(max_fragment_length, 0, -1):
            fragment = content[-fragment_length:]
            if any(marker.startswith(fragment) for marker in markers):
                return fragment
        return ""

    def _log_provider_failure(self, provider_name: str, exc: Exception) -> None:
        if self._is_rate_limit_error(exc):
            logger.warning(
                "[LLM Router] Provider rate limited",
                provider=provider_name,
                error=str(exc),
            )
            blocked_until = self._activate_circuit_breaker(provider_name)
            logger.warning(
                "[LLM Router] Circuit breaker activated",
                provider=provider_name,
                blocked_until=blocked_until,
                cooldown_seconds=PROVIDER_COOLDOWN_SECONDS,
            )
            return

        logger.warning(
            "[LLM Router] Provider failed",
            provider=provider_name,
            error=str(exc),
        )

    def _activate_circuit_breaker(self, provider_name: str) -> float:
        blocked_until = time.time() + PROVIDER_COOLDOWN_SECONDS
        self.provider_status[provider_name]["blockedUntil"] = blocked_until
        return blocked_until

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        status_codes = set()
        for attr in ("status_code", "status"):
            value = getattr(exc, attr, None)
            if isinstance(value, int):
                status_codes.add(value)

        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) is not None:
            status_codes.add(response.status_code)

        code = getattr(exc, "code", None)
        if isinstance(code, int):
            status_codes.add(code)
        elif code is not None:
            code_text = getattr(code, "name", str(code)).lower()
            if any(marker in code_text for marker in RATE_LIMIT_MARKERS):
                return True

        if 429 in status_codes:
            return True

        message_parts = [str(exc)]
        if response is not None:
            try:
                message_parts.append(response.text)
            except Exception:
                pass
        body = getattr(exc, "body", None)
        if body is not None:
            message_parts.append(str(body))

        message = " ".join(part for part in message_parts if part).lower()
        return any(marker in message for marker in RATE_LIMIT_MARKERS)
