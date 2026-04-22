from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Request

from services.message_workflow import MessageWorkflow


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_process_message_invokes_handler() -> None:
    workflow = MessageWorkflow()
    request = Request({"type": "http", "method": "POST", "path": "/messages", "headers": []}, _receive)
    observed: dict[str, object] = {}

    async def _handler(req: Request, auth_data: dict):
        observed["path"] = req.url.path
        observed["user"] = auth_data.get("user_id")
        return SimpleNamespace(status_code=200)

    response = await workflow.process_message(
        request=request,
        auth_data={"user_id": "u-1"},
        handler=_handler,
    )

    assert response.status_code == 200
    assert observed == {"path": "/messages", "user": "u-1"}


@pytest.mark.asyncio
async def test_run_stage_propagates_errors() -> None:
    workflow = MessageWorkflow()

    async def _boom() -> None:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await workflow.run_stage("explode", _boom)
