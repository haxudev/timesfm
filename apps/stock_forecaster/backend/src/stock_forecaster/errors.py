from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
  def __init__(
    self,
    code: str,
    message: str,
    status_code: int = 400,
    details: Any | None = None,
  ) -> None:
    super().__init__(message)
    self.code = code
    self.message = message
    self.status_code = status_code
    self.details = details


def error_payload(request: Request, error: AppError) -> dict[str, Any]:
  body: dict[str, Any] = {
    "error": {
      "code": error.code,
      "message": error.message,
      "request_id": getattr(request.state, "request_id", "unknown"),
    }
  }
  if error.details is not None:
    body["error"]["details"] = error.details
  return body


def install_error_handlers(app: FastAPI) -> None:
  @app.exception_handler(AppError)
  async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
      status_code=error.status_code,
      content=error_payload(request, error),
    )

  @app.exception_handler(RequestValidationError)
  async def handle_validation(
    request: Request, error: RequestValidationError
  ) -> JSONResponse:
    safe_details = [
      {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
      for item in error.errors()
    ]
    app_error = AppError(
      "validation_error",
      "The request contains invalid values.",
      422,
      safe_details,
    )
    return JSONResponse(
      status_code=422,
      content=error_payload(request, app_error),
    )

  @app.exception_handler(Exception)
  async def handle_unexpected(request: Request, _: Exception) -> JSONResponse:
    app_error = AppError(
      "internal_error",
      "The server could not complete the request.",
      500,
    )
    return JSONResponse(
      status_code=500,
      content=error_payload(request, app_error),
    )
