"""Fallback response generation - consolidates timeout handling."""

import asyncio
from typing import Any

from api.logging_config import logger
from api.services.inference import generate_explanation
from api.utils import SOCRATIC_MODE, TECHNICAL_MODE


class FallbackResponseGenerator:
    """
    Generates fallback responses when streaming times out or fails.
    
    Consolidates:
    - _run_fallback_generation() calls (appears 4+ times)
    - Timeout fallback messages
    - Error handling
    
    Benefits:
    - Single try-catch strategy
    - Consistent error messages per mode
    - Testable fallback logic
    """
    
    def __init__(self, inference_service: Any):
        """Initialize with inference service."""
        self.inference = inference_service
    
    async def generate(
        self,
        content: str,
        context: Any,  # MessageContext
        timeout_seconds: float,
    ) -> str:
        """Generate fallback response with timeout.
        
        Args:
            content: User message content
            context: MessageContext with all config
            timeout_seconds: Timeout for generation
            
        Returns:
            Generated response or fallback message
        """
        try:
            result = await asyncio.wait_for(
                generate_explanation(
                    content,
                    context.prompt_mode,
                    mode=context.llm_mode,
                    temperature=context.temperature,
                    regenerate=context.regenerate,
                    request_id=context.request_id,
                    user_id=context.user_id,
                    is_pro=context.is_pro,
                    telemetry_sink={},  # Handle separately
                    conversation_messages=context.context_messages,
                    conversation_context="",  # Build from history
                    intent_system_prompt="",  # Include from context
                ),
                timeout=timeout_seconds,
            )
            return str(result)
        except asyncio.TimeoutError:
            logger.warning(
                "fallback_generation_timeout",
                request_id=context.request_id,
                mode=context.selected_mode,
                timeout_seconds=timeout_seconds,
            )
            return self.get_timeout_fallback(context.selected_mode)
        except Exception as exc:
            logger.error(
                "fallback_generation_failed",
                error=str(exc),
                request_id=context.request_id,
                mode=context.selected_mode,
            )
            return self.get_timeout_fallback(context.selected_mode)
    
    @staticmethod
    def get_timeout_fallback(mode: str) -> str:
        """Get appropriate fallback message for mode.
        
        Args:
            mode: Selected mode (technical, socratic, chat)
            
        Returns:
            User-friendly fallback message
            
        Note: Replaces _final_fallback_message() + duplicated messages
        """
        if mode == TECHNICAL_MODE:
            return (
                "Unable to generate a complete technical response right now due to a "
                "transient timeout. Please retry in a moment."
            )
        elif mode == SOCRATIC_MODE:
            return (
                "Unable to generate a complete socratic response right now due to a "
                "transient timeout. Please retry in a moment."
            )
        return (
            "Unable to generate a complete response right now due to a transient timeout. "
            "Please retry in a moment."
        )
