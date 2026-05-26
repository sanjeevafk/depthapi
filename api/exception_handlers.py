"""Exception handler registration for FastAPI."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.monitoring import capture_exception
from api.services.inference.llm_errors import LLMError, LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable


def register_exception_handlers(app: FastAPI) -> None:
    """Register API exception handlers."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        capture_exception(exc, request_id=getattr(request.state, "request_id", None), path=request.url.path)
        return JSONResponse(status_code=500, content={"error": "Internal server error"})

    @app.exception_handler(LLMUnavailable)
    async def llm_unavailable_handler(request: Request, exc: LLMUnavailable):
        return JSONResponse(status_code=503, content={"error": {"type": "service_degraded", "message": str(exc)}})

    @app.exception_handler(LLMInvalidAPIKey)
    async def llm_invalid_api_key_handler(request: Request, exc: LLMInvalidAPIKey):
        return JSONResponse(status_code=502, content={"error": {"type": "invalid_api_key", "message": str(exc)}})

    @app.exception_handler(LLMBadRequest)
    async def llm_bad_request_handler(request: Request, exc: LLMBadRequest):
        return JSONResponse(status_code=400, content={"error": {"type": "bad_request", "message": str(exc)}})

    @app.exception_handler(LLMError)
    async def llm_error_handler(request: Request, exc: LLMError):
        return JSONResponse(status_code=400, content={"error": {"type": "llm_error", "message": str(exc)}})
