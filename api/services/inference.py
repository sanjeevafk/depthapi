"""LiteLLM-backed inference service."""

import asyncio
import json
import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import get_settings
from prompts import PROMPTS, TECHNICAL_DEPTH_PROMPT
from logging_config import logger, anonymize_user_id, log_sampled_success
from services.search import search_service
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode
from services.llm_client import close_llm_client, create_chat_completion, stream_chat_completion



async def close_client():
    """Close shared LLM client resources."""
    await close_llm_client()
def _extract_usage_dict(usage_obj) -> dict[str, int] | None:
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        usage_obj = usage_obj.model_dump()
    elif hasattr(usage_obj, "dict"):
        usage_obj = usage_obj.dict()
    if not isinstance(usage_obj, dict):
        return None

    prompt_tokens = usage_obj.get("prompt_tokens")
    completion_tokens = usage_obj.get("completion_tokens")
    total_tokens = usage_obj.get("total_tokens")
    try:
        return {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(total_tokens or 0),
        }
    except (TypeError, ValueError):
        return None


def _extract_estimated_cost(result, usage: dict[str, int] | None) -> float | None:
    direct_cost = getattr(result, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        return float(direct_cost)

    hidden_params = getattr(result, "_hidden_params", None)
    if isinstance(hidden_params, dict):
        hidden_cost = hidden_params.get("response_cost")
        if isinstance(hidden_cost, (int, float)):
            return float(hidden_cost)

    if isinstance(usage, dict):
        usage_cost = usage.get("cost")
        if isinstance(usage_cost, (int, float)):
            return float(usage_cost)

    return None


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 1024, **kwargs) -> str:
    """Call API with given model and prompt."""
    task = kwargs.get("task", "general")
    if model in ["openai/gpt-oss-20b", "gpt-oss-20b", "deep_dive"]:
        task = "coding"
            
    try:
        alias = model or "default-fast"
        request_id = kwargs.get("request_id")
        retry_flag = bool(kwargs.get("regenerate", False))
        anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
        telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
        model_start = time.perf_counter()
        result = await create_chat_completion(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
        )
        model_inference_ms = round((time.perf_counter() - model_start) * 1000, 2)
        usage = _extract_usage_dict(getattr(result, "usage", None))
        estimated_cost_usd = _extract_estimated_cost(result, usage)
        model_name = getattr(result, "model", None)
        if telemetry_sink is not None:
            telemetry_sink["token_usage"] = usage
            telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
            telemetry_sink["model_inference_ms"] = model_inference_ms
            telemetry_sink["model_alias"] = alias

        log_sampled_success(
            "llm_completion_observed",
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            model_alias=alias,
            model=model_name,
            latency_ms=model_inference_ms,
            token_usage=usage,
            estimated_cost_usd=estimated_cost_usd,
            retry=retry_flag,
            sampled=True,
        )
        if not result.choices:
            raise RuntimeError("LLM response missing choices.")
        return result.choices[0].message.content or ""
    except Exception as e:
        logger.error(
            "inference_failed",
            error=str(e),
            model_alias=model or "default-fast",
            request_id=kwargs.get("request_id"),
            user_id_hash=anonymize_user_id(str(kwargs.get("user_id") or "") or None),
            retry=bool(kwargs.get("regenerate", False)),
            sampled=False,
        )
        raise



async def generate_explanation(topic: str, level: str, model: str | None = None, **kwargs) -> str:
    """Generate explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    
    if mode == TECHNICAL_MODE:
        search_task = search_service.get_structured_search_context(topic)
        image_task = search_service.get_images(topic)
        quote_task = search_service.get_quote()
        
        structured_context, images, quote = await asyncio.gather(search_task, image_task, quote_task)
        
        prompt = TECHNICAL_DEPTH_PROMPT.format(
            search_context=json.dumps(structured_context, ensure_ascii=True, indent=2),
            quote_text=quote if quote else "No specific quote found.",
            topic=topic
        )
        
        response = await call_model(model or "technical-primary", prompt, **kwargs)
        
        # Append images if available
        if images:
             response += "\n\n### Visual References\n"
             for img in images:
                 response += f"![{img.get('title', 'Image')}]({img['url']})\n"
        
        return response

    if mode == SOCRATIC_MODE:
        template = PROMPTS.get("socratic")
        if not template:
            raise ValueError("Unknown mode template: socratic")
        prompt = template.format(
            topic=topic,
            conversation_context=kwargs.get("conversation_context", "No prior context."),
        )
        return await call_model(model or "socratic", prompt, **kwargs)

    template = PROMPTS.get(level)
    if not template:
        raise ValueError(f"Unknown level: {level}")
        
    prompt = template.format(topic=topic)
        
    return await call_model(model or "default-fast", prompt, **kwargs)
async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs):
    """Stream explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    route_telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
    prompt = ""
    images = []

    if mode == TECHNICAL_MODE:
        search_task = search_service.get_structured_search_context(topic)
        image_task = search_service.get_images(topic)
        quote_task = search_service.get_quote()
        
        structured_context, images_result, quote = await asyncio.gather(search_task, image_task, quote_task)
        images = images_result
        
        prompt = TECHNICAL_DEPTH_PROMPT.format(
            search_context=json.dumps(structured_context, ensure_ascii=True, indent=2),
            quote_text=quote if quote else "No specific quote found.",
            topic=topic
        )
    elif mode == SOCRATIC_MODE:
        template = PROMPTS.get("socratic")
        if not template:
            raise ValueError("Unknown mode template: socratic")
        prompt = template.format(
            topic=topic,
            conversation_context=kwargs.get("conversation_context", "No prior context."),
        )
    else:
        template = PROMPTS.get(level)
        if not template:
            raise ValueError(f"Unknown level: {level}")
        prompt = template.format(topic=topic)
    
    alias = model or (
        "technical-primary"
        if mode == TECHNICAL_MODE
        else "socratic"
        if mode == SOCRATIC_MODE
        else "default-fast"
    )
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    async for chunk in stream_chat_completion(
        model=alias,
        messages=[{"role": "user", "content": prompt}],
        temperature=kwargs.get("temperature", 0.7),
        request_id=request_id,
        telemetry_sink=stream_telemetry,
    ):
        yield chunk

    stream_duration_ms = round((time.perf_counter() - stream_start) * 1000, 2)
    model_inference_ms = stream_telemetry.get("model_inference_ms")
    token_usage = stream_telemetry.get("token_usage")
    estimated_cost_usd = stream_telemetry.get("estimated_cost_usd")
    model_name = stream_telemetry.get("model")

    if route_telemetry_sink is not None:
        route_telemetry_sink["token_usage"] = token_usage
        route_telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
        route_telemetry_sink["model_inference_ms"] = model_inference_ms
        route_telemetry_sink["stream_duration_ms"] = stream_duration_ms
        route_telemetry_sink["model_alias"] = alias
        route_telemetry_sink["model"] = model_name

    log_sampled_success(
        "llm_stream_observed",
        request_id=request_id,
        user_id_hash=anonymized_user_id,
        model_alias=alias,
        model=model_name,
        latency_ms=model_inference_ms,
        stream_duration_ms=stream_duration_ms,
        token_usage=token_usage,
        estimated_cost_usd=estimated_cost_usd,
        retry=retry_flag,
        sampled=True,
    )

    # Append images at the end of the stream for technical depth
    if mode == TECHNICAL_MODE and images:
        yield "\n\n### Visual References\n"
        for img in images:
            if url := img.get('url'):
                yield f"![{img.get('title', 'Image')}]({url})\n"
