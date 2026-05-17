from __future__ import annotations

from api.prompt_engine import PromptSpec
from api.routers.messages_core import MessageRequest
from api.routers.query import QueryRequest


def test_query_request_accepts_prompt_spec_axes() -> None:
    req = QueryRequest.model_validate(
        {
            "topic": "Redis vs Postgres",
            "prompt_spec": {
                "depth": "technical",
                "task": "compare",
                "reasoning": "direct",
                "style": "academic",
            },
        }
    )
    spec = req.prompt_spec.to_prompt_spec(req.topic) if req.prompt_spec else None
    assert isinstance(spec, PromptSpec)
    assert spec.depth == "technical"
    assert spec.task == "compare"


def test_message_request_accepts_prompt_spec_axes() -> None:
    req = MessageRequest.model_validate(
        {
            "conversation_id": "c1",
            "content": "Walk me through Raft",
            "prompt_spec": {
                "depth": "technical",
                "task": "analyze",
                "reasoning": "guided",
                "style": "normal",
            },
        }
    )
    spec = req.prompt_spec.to_prompt_spec(req.content) if req.prompt_spec else None
    assert isinstance(spec, PromptSpec)
    assert spec.reasoning == "guided"
