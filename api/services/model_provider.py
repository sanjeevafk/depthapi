"""Model provider abstraction and fallback routing."""

import os
import re
import time
from typing import Any

import httpx
from google import genai
from groq import AsyncGroq

from config import get_settings
from logging_config import logger

GROQ_PROVIDER = "groq"
GEMINI_PROVIDER = "gemini"
OPENROUTER_PROVIDER = "openrouter"
HUGGINGFACE_PROVIDER = "huggingface"

LEARNING_PROVIDER_ORDER = (
    GROQ_PROVIDER,
    GEMINI_PROVIDER,
    OPENROUTER_PROVIDER,
    HUGGINGFACE_PROVIDER,
)
TECHNICAL_PROVIDER_ORDER = (
    GEMINI_PROVIDER,
    GROQ_PROVIDER,
    OPENROUTER_PROVIDER,
    HUGGINGFACE_PROVIDER,
)

PROVIDER_COOLDOWN_SECONDS = 24 * 60 * 60
GEMINI_PRIMARY_MODEL = "gemini-2.5-pro"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_ALLOWLIST = {
    GEMINI_PRIMARY_MODEL,
    GEMINI_FALLBACK_MODEL,
}
OPENROUTER_PRIMARY_MODEL = "qwen/qwen3.5-9b"
OPENROUTER_FALLBACK_MODEL = "anthropic/claude-sonnet-4.6"
OPENROUTER_MODEL_ALLOWLIST = {
    OPENROUTER_PRIMARY_MODEL,
    OPENROUTER_FALLBACK_MODEL,
}

GROQ_MODEL_ALLOWLIST = {
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
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
        )
        self.openrouter_fallback_model = self._validated_model(
            getattr(self.settings, "openrouter_fallback_model", "")
            or os.getenv("OPENROUTER_FALLBACK_MODEL", OPENROUTER_FALLBACK_MODEL),
            OPENROUTER_MODEL_ALLOWLIST,
            OPENROUTER_FALLBACK_MODEL,
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
        self.hf_model = getattr(self.settings, "huggingface_model", "") or os.getenv("HUGGINGFACE_MODEL", "")
        self.hf_classification_model = (
            getattr(self.settings, "huggingface_classification_model", "")
            or os.getenv("HUGGINGFACE_CLASSIFICATION_MODEL", "")
        )
        self.http_client = httpx.AsyncClient(timeout=15.0)
        from typing import Optional

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
        if task == "classification":
            classification_result = await self._call_huggingface_classification(prompt)
            if classification_result is not None:
                return classification_result

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
            return await self._call_huggingface(prompt)
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
            return bool(self.hf_token and self.hf_model)
        return False

    def _route_label(self, mode: str, image_data, prompt: str) -> str:
        normalized_mode = (mode or "").lower()
        if image_data or len(prompt) > 20000 or normalized_mode == "technical_depth":
            return "technical"
        return "learning"

    def _resolve_groq_model(self, prompt: str, task="general", **kwargs) -> tuple[str, int]:
        target_model = "llama-3.1-8b-instant"
        max_tokens = 1024
        mode = (kwargs.get("mode") or "").lower()

        if mode == "technical_depth":
            target_model = "llama-3.3-70b-versatile"
            max_tokens = 3000
        elif task == "coding" or "code" in task.lower():
            target_model = "llama-3.3-70b-versatile"
            max_tokens = 2048
        elif mode == "fast":
            max_tokens = 1200
        elif mode in {"eli5", "eli10"}:
            max_tokens = 1200

        requested_model = kwargs.get("model")
        if requested_model in GROQ_MODEL_ALLOWLIST:
            target_model = requested_model

        return target_model, max_tokens

    async def _call_groq(self, prompt: str, task="general", **kwargs) -> dict:
        if not self.groq_client:
            raise ModelUnavailable("Groq is not configured.")

        target_model, max_tokens = self._resolve_groq_model(prompt, task=task, **kwargs)
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

    async def _call_openrouter(self, prompt: str, task="general", **kwargs) -> dict:
        if not self.openrouter_api_key:
            raise ModelUnavailable("OpenRouter is not configured.")

        _, max_tokens = self._resolve_groq_model(prompt, task=task, **kwargs)
        target_model = self._resolve_openrouter_model(**kwargs)
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

    async def _call_huggingface(self, prompt: str) -> dict:
        if not self.hf_token or not self.hf_model:
            raise ModelUnavailable("HuggingFace is not configured.")

        response = await self.http_client.post(
            f"https://api-inference.huggingface.co/models/{self.hf_model}",
            headers={"Authorization": f"Bearer {self.hf_token}"},
            json={
                "inputs": f"<|user|>\n{prompt}<|end|>\n<|assistant|>",
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
        return {
            "provider": HUGGINGFACE_PROVIDER,
            "model": self.hf_model,
            "content": self._clean_text(content),
        }

    async def _call_huggingface_classification(self, prompt: str) -> dict | None:
        if not self.hf_token or not self.hf_classification_model:
            return None
        if HUGGINGFACE_PROVIDER not in self._available_providers((HUGGINGFACE_PROVIDER,)):
            return None

        try:
            response = await self.http_client.post(
                f"https://api-inference.huggingface.co/models/{self.hf_classification_model}",
                headers={"Authorization": f"Bearer {self.hf_token}"},
                json={"inputs": prompt},
                timeout=5.0,
            )
            response.raise_for_status()
            return {
                "provider": HUGGINGFACE_PROVIDER,
                "model": self.hf_classification_model,
                "content": str(response.json()),
            }
        except Exception as exc:
            self._log_provider_failure(HUGGINGFACE_PROVIDER, exc)
            return None

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

        response = self.gemini_client.models.generate_content(
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
        mode = (kwargs.get("mode") or "").lower()
        if image_data is not None or mode == "technical_depth":
            return self.gemini_primary_model
        return self.gemini_fallback_model

    def _resolve_openrouter_model(self, **kwargs) -> str:
        requested_model = kwargs.get("model")
        if requested_model in OPENROUTER_MODEL_ALLOWLIST:
            return requested_model
        mode = (kwargs.get("mode") or "").lower()
        if mode == "technical_depth":
            return self.openrouter_fallback_model
        return self.openrouter_primary_model

    def _validated_model(self, configured_model: str, allowed_models: set[str], fallback_model: str) -> str:
        if configured_model in allowed_models:
            return configured_model
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
