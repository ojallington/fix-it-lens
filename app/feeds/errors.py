"""Every error body is the contract's Error {error, detail} (api.openapi.yaml), whichever route raised it.

    from app.feeds.errors import install_errors
    install_errors(app)   # in create_app, once

An HTTPException's status names `error`; its detail string becomes `detail`. A detail that is already an Error dict
passes through, and so does a CapError's reopens_at (a 429 body). Request validation is error "invalid" with the
first problem as the detail. Anything unhandled is error "internal" with no detail beyond that, so a traceback
never reaches a phone.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("fixit.errors")

CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 413: "too_large", 422: "invalid", 429: "too_many_requests", 500: "internal",
    502: "bad_gateway", 503: "unavailable", 504: "timeout",
}


def error_body(status: int, detail: Any = None) -> dict[str, Any]:
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        return {"error": detail["error"], **({"reopens_at": detail["reopens_at"]} if "reopens_at" in detail else {}),
                **({"detail": str(detail["detail"])} if "detail" in detail else {})}
    body: dict[str, Any] = {"error": CODES.get(status, "error")}
    if detail is not None and str(detail):
        body["detail"] = str(detail)
    return body


def install_errors(app: FastAPI) -> None:
    app.state.contract_errors = True

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(error_body(e.status_code, e.detail), e.status_code, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", ()) if x != "body")
        return JSONResponse(error_body(422, f"{where}: {first.get('msg', 'invalid')}" if where else "invalid"), 422)

    @app.exception_handler(Exception)
    async def internal(request: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(error_body(500), 500)
