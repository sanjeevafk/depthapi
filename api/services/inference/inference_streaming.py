"""Streaming inference flow extracted from inference service."""

from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator, Callable, Awaitable, cast

import structlog
from openai.types.chat import ChatCompletionMessageParam

from api.config import get_settings
from api.logging_config import anonymize_user_id, log_sampled_success
from api.services.inference.inference_constants import (
    TECHNICAL_MAX_TOKENS,
    TECHNICAL_MINIMAL_PROMPT,
    TECHNICAL_TEMPERATURE,
)
from api.services.inference.inference_routing import _learning_model_for_level
from api.services.inference.inference_search import (
    _append_rag_context,
    _append_search_context,
    format_rag_context,
)
from api.services.rag.rag_backend_router import retrieve_context as retrieve_rag_context
import os

_tech_logger = structlog.get_logger(__name__)


async def generate_stream_explanation(
    topic: str,
    level: str,
    model: str | None = None,
    *,
    normalize_mode_fn: Callable[[str | None], str],
    load_search_context_fn: Callable[..., Awaitable[str]],
    detect_intent_and_depth_fn: Callable[[str], dict[str, str]],
    detect_diagram_type_fn: Callable[[str], str | None],
    build_technical_prompt_fn: Callable[[str, str, str, str | None], str],
    build_prompt_fn: Callable[..., str],
    build_messages_fn: Callable[..., list[dict[str, str]]],
    stream_chat_completion_fn: Callable[..., AsyncGenerator[str, None]],
    technical_mode_handler_fn: Callable[..., Awaitable[str]],
    technical_route_fn: Callable[..., tuple[str, str]],
    model_router,
    response_builder,
    prompt_orchestrator,
    wants_direct_answer_fn: Callable[[str], bool],
    enforce_socratic_response_constraints_fn: Callable[..., str],
    normalize_question_signature_fn: Callable[[str], str],
    word_count_fn: Callable[[str], int],
    normalize_whitespace_fn: Callable[[str], str],
    compress_sentence_fn: Callable[[str, int], str],
    append_cue_if_fits_fn: Callable[[str, int, str | None], str],
    **kwargs: Any,
) -> AsyncGenerator[str, None]:
    mode = normalize_mode_fn(kwargs.get("mode", "learn"))
    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    route_telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
    prompt = ""

    if mode == "technical":
        intent = "unknown"
        depth = "shallow"
        diagram_type = "generic"
        try:
            classification = detect_intent_and_depth_fn(topic)
            intent = classification["intent"]
            depth = classification["depth"]
            diagram_type = detect_diagram_type_fn(topic)
        except Exception as exc:
            _tech_logger.warning(
                "technical_stream_classification_failed",
                error=str(exc),
                intent=intent,
                depth=depth,
                diagram_type=diagram_type,
            )

        # 1. RAG Retrieval
        rag_context = ""
        try:
            rag_results = await retrieve_rag_context(
                query=topic,
                api_key_id=str(kwargs.get("user_id") or "anonymous"),
                limit=int(os.getenv("RAG_TOP_K", "5")),
                collection_id=kwargs.get("collection_id"),
                use_trusted_corpus=kwargs.get("use_trusted_corpus", True),
                query_mode="technical",
            )
            rag_context = format_rag_context(rag_results)
        except Exception as exc:
            logger.error(f"technical_stream_rag_failed: {str(exc)}", request_id=kwargs.get("request_id"))

        # 2. Web Search
        search_context = await load_search_context_fn(topic, mode="technical")
        
        prompt = build_technical_prompt_fn(topic, intent, depth, diagram_type)
        if not prompt or not prompt.strip():
            prompt = TECHNICAL_MINIMAL_PROMPT
            
        # Append RAG context
        if rag_context:
            prompt = _append_search_context(prompt, f"--- RAG CONTEXT ---\n{rag_context}\n--- END RAG CONTEXT ---")
            
        prompt = _append_search_context(prompt, search_context)
        messages = build_messages_fn(
            prompt,
            conversation_messages=kwargs.get("conversation_messages"),
            intent_system_prompt=kwargs.get("intent_system_prompt"),
            mode=mode,
        )

        primary_alias, _fallback_alias = technical_route_fn(
            topic,
            intent=intent,
            depth=depth,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        alias = model or primary_alias
        stream_telemetry: dict[str, object] = {}
        stream_start = time.perf_counter()
        streamed_chunks = 0
        stream_completed = True
        partial_failure = False

        try:
            async for chunk in stream_chat_completion_fn(
                model=alias,
                messages=cast(list[ChatCompletionMessageParam], messages),
                max_tokens=TECHNICAL_MAX_TOKENS,
                temperature=TECHNICAL_TEMPERATURE,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                streamed_chunks += 1
                yield chunk
        except Exception as exc:
            _tech_logger.warning(
                "technical_stream_failed",
                error=str(exc),
                streamed_chunks=streamed_chunks,
                model_alias=alias,
            )
            if streamed_chunks == 0:
                full_response = await technical_mode_handler_fn(topic, _search_context=search_context, **kwargs)
                for index in range(0, len(full_response), 400):
                    yield full_response[index : index + 400]
            else:
                stream_completed = False
                partial_failure = True
                _tech_logger.warning(
                    "technical_stream_partial_failure",
                    error=str(exc),
                    streamed_chunks=streamed_chunks,
                    model_alias=alias,
                    partial_failure=True,
                )
                yield "\n\n---\n*Response incomplete due to a service interruption.*"
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
            route_telemetry_sink["stream_completed"] = stream_completed
            route_telemetry_sink["partial_failure"] = partial_failure

        if stream_completed:
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
        else:
            _tech_logger.warning(
                "llm_stream_observed_partial_failure",
                request_id=request_id,
                user_id_hash=anonymized_user_id,
                model_alias=alias,
                model=model_name,
                latency_ms=model_inference_ms,
                stream_duration_ms=stream_duration_ms,
                token_usage=token_usage,
                estimated_cost_usd=estimated_cost_usd,
                retry=retry_flag,
                streamed_chunks=streamed_chunks,
                partial_failure=True,
            )
        return

    length_constraint: tuple[str, int] | None = None
    if mode == "socratic":
        search_context = await load_search_context_fn(topic, mode="socratic")
        prompt = build_prompt_fn(
            "socratic",
            topic,
            conversation_context=kwargs.get("conversation_context", ""),
        )
        prompt = _append_search_context(prompt, search_context)
    else:
        # 1. RAG Retrieval
        rag_context = ""
        try:
            rag_results = await retrieve_rag_context(
                query=topic,
                api_key_id=str(kwargs.get("user_id") or "anonymous"),
                limit=int(os.getenv("RAG_TOP_K", "5")),
                collection_id=kwargs.get("collection_id"),
                use_trusted_corpus=kwargs.get("use_trusted_corpus", True),
                query_mode="conceptual",
            )
            rag_context = format_rag_context(rag_results)
        except Exception as exc:
            logger.error(f"learn_stream_rag_failed: {str(exc)}", request_id=kwargs.get("request_id"))

        # 2. Web Search
        search_context = await load_search_context_fn(topic, mode="learn")
        
        # 3. Assemble Prompt
        prompt = build_prompt_fn(level, topic)
        prompt = _append_rag_context(prompt, rag_context)
        prompt = _append_search_context(prompt, search_context)
        length_constraint = prompt_orchestrator.extract_length_constraint(topic)
        prompt = prompt_orchestrator.apply_length_constraints(prompt, length_constraint)

    if model:
        alias = model
    else:
        ranked_aliases = model_router.route_aliases(
            topic,
            intent=None,
            mode=mode,
            level=level,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        alias = ranked_aliases[0] if ranked_aliases else (
            "socratic" if mode == "socratic" else _learning_model_for_level(level)
        )
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    if mode == "socratic":
        socratic_raw_chunks: list[str] = []
        pending = ""
        seen_signatures: set[str] = set()
        emitted_count = 0
        wants_direct_answer = wants_direct_answer_fn(topic)
        socratic_error: Exception | None = None
        max_questions = 3
        footer = "Share your answer, and I will guide the next step."

        try:
            settings = get_settings()
            max_tokens = int(getattr(settings, "max_output_tokens_socratic", 1024))
            async for chunk in stream_chat_completion_fn(
                model=alias,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    build_messages_fn(
                        prompt,
                        conversation_messages=kwargs.get("conversation_messages"),
                        intent_system_prompt=kwargs.get("intent_system_prompt"),
                        mode=mode,
                    ),
                ),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=max_tokens,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                text_chunk = str(chunk or "")
                socratic_raw_chunks.append(text_chunk)
                if wants_direct_answer or emitted_count >= max_questions:
                    continue

                pending += text_chunk
                while True:
                    match = re.search(r"[^?]*\?", pending)
                    if not match:
                        break

                    candidate = match.group(0).strip()
                    consumed = match.end()
                    pending = pending[consumed:]
                    if not candidate:
                        continue

                    signature = normalize_question_signature_fn(candidate)
                    if not signature or signature in seen_signatures:
                        continue

                    seen_signatures.add(signature)
                    yield candidate + " "
                    emitted_count += 1

                    if emitted_count >= max_questions:
                        yield footer
                        break
        except Exception as exc:
            socratic_error = exc
            stream_telemetry["stream_error"] = str(exc)
            stream_telemetry["stream_error_type"] = type(exc).__name__
            stream_telemetry["request_id"] = request_id
            _tech_logger.warning(
                "socratic_stream_failed",
                request_id=request_id,
                model_alias=alias,
                error=str(exc),
            )

        if wants_direct_answer or emitted_count == 0:
            constrained_response = enforce_socratic_response_constraints_fn(
                "".join(socratic_raw_chunks),
                topic=topic,
                wants_direct_answer=wants_direct_answer,
            )
            fallback_response = constrained_response.strip()
            if socratic_error is not None and not fallback_response:
                fallback_response = f"I hit a temporary issue while streaming. Please try again. {footer}"
            elif socratic_error is not None:
                if "temporary issue while streaming" not in fallback_response:
                    fallback_response = f"I hit a temporary issue while streaming. {fallback_response}"
            for index in range(0, len(fallback_response), 400):
                yield fallback_response[index : index + 400]
        elif emitted_count > 0 and emitted_count < max_questions:
            yield footer
    else:
        streamed_chunks = 0
        remaining_chars = None
        target_words = None
        words_emitted = 0
        pending = ""
        cue: str | None = None
        emitted_any = False
        trimmed_for_limit = False
        is_large_input = prompt_orchestrator.is_large_input(topic)
        if length_constraint:
            unit, count = length_constraint
            if unit == "chars":
                remaining_chars = count
            else:
                target_words = count
        elif not is_large_input:
            target_words, cue = prompt_orchestrator.learning_length_policy(topic)
        try:
            max_tokens = int(getattr(get_settings(), "max_output_tokens_learning", 1024))
            async for chunk in stream_chat_completion_fn(
                model=alias,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    build_messages_fn(
                        prompt,
                        conversation_messages=kwargs.get("conversation_messages"),
                        intent_system_prompt=kwargs.get("intent_system_prompt"),
                        mode=mode,
                    ),
                ),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=max_tokens,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                text_chunk = str(chunk or "")
                if remaining_chars is not None:
                    if remaining_chars <= 0:
                        break
                    if len(text_chunk) <= remaining_chars:
                        streamed_chunks += 1
                        remaining_chars -= len(text_chunk)
                        yield text_chunk
                    else:
                        streamed_chunks += 1
                        yield text_chunk[:remaining_chars]
                        remaining_chars = 0
                        break
                    continue

                if target_words is not None:
                    pending += text_chunk
                    sentences, pending = prompt_orchestrator.drain_complete_sentences(pending)
                    if not sentences:
                        continue
                    for sentence in sentences:
                        sentence_words = word_count_fn(sentence)
                        if words_emitted + sentence_words <= target_words:
                            streamed_chunks += 1
                            prefix = "" if not emitted_any else " "
                            yield f"{prefix}{sentence}"
                            emitted_any = True
                            words_emitted += sentence_words
                        else:
                            trimmed_for_limit = True
                            pending = ""
                            break
                    if trimmed_for_limit:
                        break
                    continue

                streamed_chunks += 1
                yield text_chunk
        except Exception as exc:
            stream_telemetry["stream_error"] = str(exc)
            stream_telemetry["stream_error_type"] = type(exc).__name__
            stream_telemetry["request_id"] = request_id
            _tech_logger.warning(
                "learning_stream_failed",
                request_id=request_id,
                model_alias=alias,
                streamed_chunks=streamed_chunks,
                error=str(exc),
            )
            if streamed_chunks == 0:
                yield "Unable to stream a response right now. Please try again."
            else:
                yield "\n\n---\n*Response incomplete due to a service interruption.*"

        if target_words is not None:
            if not trimmed_for_limit:
                final_pending = normalize_whitespace_fn(pending)
                if final_pending:
                    final_words = word_count_fn(final_pending)
                    if words_emitted + final_words <= target_words:
                        prefix = "" if not emitted_any else " "
                        yield f"{prefix}{final_pending}"
                        emitted_any = True
                        words_emitted += final_words
                    elif not emitted_any:
                        compressed = compress_sentence_fn(final_pending, target_words)
                        if compressed:
                            result = append_cue_if_fits_fn(compressed, target_words, cue)
                            yield result
                            emitted_any = True
                            words_emitted = word_count_fn(result)
            if trimmed_for_limit and cue:
                cue_words = word_count_fn(cue)
                if words_emitted + cue_words <= target_words:
                    prefix = "" if not emitted_any else " "
                    yield f"{prefix}{cue}"

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
        if "stream_error" in stream_telemetry:
            route_telemetry_sink["stream_error"] = stream_telemetry.get("stream_error")
            route_telemetry_sink["stream_error_type"] = stream_telemetry.get("stream_error_type")
            route_telemetry_sink["request_id"] = stream_telemetry.get("request_id")

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
