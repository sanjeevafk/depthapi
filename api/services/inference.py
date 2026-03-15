"""Groq inference service."""

import asyncio
import json
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import get_settings
from prompts import PROMPTS, TECHNICAL_DEPTH_PROMPT
from logging_config import logger
from services.search import search_service
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode



async def close_client():
    """No-op as ModelProvider manages its own clients."""
    pass


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 1024, **kwargs) -> str:
    """Call API with given model and prompt."""
    from services.model_provider import ModelProvider
    
    provider = ModelProvider.get_instance()
    
    task = kwargs.get("task", "general")
    if model in ["openai/gpt-oss-20b", "gpt-oss-20b", "deep_dive"]:
        task = "coding"
            
    try:
        route_kwargs = {"prompt": prompt, "task": task, **kwargs}
        if model:
            route_kwargs["model"] = model
        result = await provider.route_inference(**route_kwargs)

        return result["content"]
    except Exception as e:
         logger.error("inference_failed", error=str(e), model=model)
         raise e



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
        
        response = await call_model(model, prompt, **kwargs)
        
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
        return await call_model(model, prompt, **kwargs)

    template = PROMPTS.get(level)
    if not template:
        raise ValueError(f"Unknown level: {level}")
        
    prompt = template.format(topic=topic)
        
    return await call_model(model, prompt, **kwargs)


async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs):
    """Stream explanation for topic at given level."""
    from services.model_provider import ModelProvider
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    
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
    
    provider = ModelProvider.get_instance()
    async for chunk in provider.route_inference_stream(prompt, **kwargs):
        yield chunk

    # Append random quote if this is a regeneration
    if kwargs.get("regenerate"):
        quote = await search_service.get_regeneration_quote()
        yield f"\n\n{quote}"

    # Append images at the end of the stream for technical depth
    if mode == TECHNICAL_MODE and images:
        yield "\n\n### Visual References\n"
        for img in images:
            yield f"![{img.get('title', 'Image')}]({img['url']})\n"
