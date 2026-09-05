import asyncio
import hmac
import threading
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Header, Query

from ..errors import AppError
from .contracts import (
  InternalTimesFMBatchRequest,
  InternalTimesFMBatchResponse,
  InternalTimesFMRequest,
  InternalTimesFMResponse,
  JobResponse,
  PredictionBundle,
  PredictionRequest,
  SubmittedJob,
  SymbolsResponse,
)
from .runtime import ManagerTimesFMAdapter, read_internal_token
from .service import PredictionService, research_ticker


def install_research_api(app, settings, market, manager):
  router = APIRouter(prefix="/api/v2")
  lock = threading.Lock()
  app.state.research_service = None

  def service():
    with lock:
      if app.state.research_service is None:
        from .store import ResearchStore

        app.state.research_service = PredictionService(
          ResearchStore(settings.research_data_dir), market,
          ManagerTimesFMAdapter(manager, settings), settings,
        )
      return app.state.research_service

  @router.get("/symbols", response_model=SymbolsResponse)
  async def symbols(q: Annotated[str, Query(max_length=100)] = ""):
    return await asyncio.to_thread(lambda: service().symbols(q))

  @router.post("/predictions", response_model=SubmittedJob, status_code=202)
  async def submit(
    request: PredictionRequest,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
  ):
    ticker = research_ticker(request.ticker)
    return await asyncio.to_thread(
      lambda: service().submit(ticker, request.horizon, idempotency_key),
    )

  @router.get("/predictions/{ticker}/latest", response_model=PredictionBundle)
  async def latest(ticker: str):
    ticker = research_ticker(ticker)
    return await asyncio.to_thread(lambda: service().latest(ticker))

  @router.get("/jobs/{job_id}", response_model=JobResponse)
  async def job(job_id: str):
    return await asyncio.to_thread(lambda: service().get_job(job_id))

  @router.delete("/jobs/{job_id}", response_model=JobResponse)
  async def cancel(job_id: str):
    return await asyncio.to_thread(lambda: service().cancel(job_id))

  @router.get("/reports/latest")
  async def latest_report(kind: Literal["daily_report", "collection_report", "scheduler_event"] = "daily_report"):
    return await asyncio.to_thread(lambda: service().report(kind=kind))

  @router.get("/reports/{snapshot_id}")
  async def report(snapshot_id: str):
    return await asyncio.to_thread(lambda: service().report(snapshot_id))

  def authorize_inference(authorization, horizon):
    token = read_internal_token(settings)
    supplied = (authorization or "").encode("utf-8")
    if not hmac.compare_digest(supplied, ("Bearer " + token).encode("ascii")):
      raise AppError("internal_unauthorized", "Internal authentication failed.", 401)
    if horizon > settings.max_horizon:
      raise AppError("validation_error", "Horizon exceeds configured limit.", 422)

  @router.post("/internal/timesfm", response_model=InternalTimesFMResponse)
  async def internal_timesfm(
    request: InternalTimesFMRequest,
    authorization: Annotated[str | None, Header()] = None,
  ):
    def infer():
      authorize_inference(authorization, request.horizon)
      output, _, _ = manager.predict(
        np.ascontiguousarray(request.context, dtype=np.float32),
        request.horizon, settings.device,
      )
      return {"point": output.point.tolist(), "quantiles": output.quantiles.tolist(),
              "artifact_id": getattr(manager, "artifact_id", None)}

    return await asyncio.to_thread(infer)

  @router.post("/internal/timesfm-batch", response_model=InternalTimesFMBatchResponse)
  async def internal_timesfm_batch(
    request: InternalTimesFMBatchRequest,
    authorization: Annotated[str | None, Header()] = None,
  ):
    def infer():
      authorize_inference(authorization, request.horizon)
      outputs, _, _ = manager.predict_many(
        [np.ascontiguousarray(context, dtype=np.float32) for context in request.contexts],
        request.horizon, settings.device,
      )
      return {
        "outputs": [{"point": output.point.tolist(), "quantiles": output.quantiles.tolist()}
                    for output in outputs],
        "artifact_id": getattr(manager, "artifact_id", None),
      }

    return await asyncio.to_thread(infer)

  app.include_router(router)