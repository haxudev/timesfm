import json
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..errors import AppError
from ..features import log_returns, validate_prices
from ..forecasting import _china_calendar, future_business_days
from ..instruments import INDEX_NAMES
from ..market_data import normalize_frame, normalize_ticker
from ..model import validate_output
from ..schemas import DateSelection
from .contracts import ComponentState, PredictionBundle, PredictionRequest, Symbol
from .runtime import (
  PredictionRuntime,
  digest,
  latest_snapshot,
  price_snapshot,
  safe_code,
)


def research_ticker(value: str) -> str:
  ticker = normalize_ticker(value)
  if ticker in INDEX_NAMES or re.fullmatch(
    r"(?:(?:60|68)\d{4}\.SS|(?:00|30)\d{4}\.SZ|(?:4\d|8\d|92)\d{4}\.BJ)",
    ticker,
  ):
    return ticker
  raise AppError("invalid_ticker", "A supported mainland stock or index is required.", 422)


class PredictionService:
  def __init__(self, store, market, model, settings):
    self.store = store
    self.market = market
    self.model = model
    self.settings = settings
    self.runtime = PredictionRuntime(store)
    self.clock = lambda: datetime.now(timezone.utc)

  def identity(self):
    return {
      "schema_version": 2,
      "checkpoint": self.settings.checkpoint,
      "checkpoint_revision": self.settings.checkpoint_revision,
      "device": self.settings.device,
      "model_enabled": self.settings.model_enabled,
      "context_length": min(512, self.settings.default_context_length),
      "max_horizon": self.settings.max_horizon,
    }

  def symbols(self, query):
    symbols = {
      ticker: Symbol(ticker=ticker, name=name, instrument_type="index")
      for ticker, name in INDEX_NAMES.items()
    }
    snapshot = latest_snapshot(self.store, "universe")
    if snapshot is not None:
      for row in self.store.read_snapshot(snapshot[0]).to_dict("records"):
        try:
          ticker = research_ticker(row["ticker"])
          name = row.get("name")
          symbols[ticker] = Symbol(
            ticker=ticker, name=name if isinstance(name, str) and name else None,
            instrument_type="index" if ticker in INDEX_NAMES else "stock",
          )
        except (AppError, KeyError, TypeError, ValueError):
          continue
    text = query.strip().casefold()
    direct_ticker = None
    try:
      direct_ticker = research_ticker(query)
      symbols.setdefault(
        direct_ticker, Symbol(ticker=direct_ticker, instrument_type="stock"),
      )
    except AppError:
      pass
    return {"items": [
      symbol.model_dump() for symbol in symbols.values()
      if text in symbol.ticker.casefold() or text in (symbol.name or "").casefold()
      or symbol.ticker == direct_ticker
    ][:50]}

  def get_job(self, job_id):
    try:
      job = self.store.get_job(job_id)
    except ValueError:
      job = None
    if job is None or job["kind"] != "prediction":
      raise AppError("job_not_found", "Prediction job not found.", 404)
    return {key: job[key] for key in ("id", "status", "result", "error", "cancellation_requested")}

  def cancel(self, job_id):
    self.get_job(job_id)
    if not self.store.cancel_job(job_id):
      raise AppError("job_terminal", "Terminal jobs cannot be cancelled.", 409)
    return self.get_job(job_id)

  def latest(self, ticker):
    result = self.store.latest_result(research_ticker(ticker))
    if result is None:
      raise AppError("prediction_not_found", "No stored prediction exists.", 404)
    return result

  def report(self, snapshot_id=None, kind="daily_report"):
    allowed = {"daily_report", "collection_report", "scheduler_event"}
    if kind not in allowed:
      raise AppError("validation_error", "Unsupported report kind.", 422)
    try:
      if snapshot_id is None:
        latest = latest_snapshot(self.store, kind)
        if latest is None:
          raise KeyError(kind)
        snapshot_id = latest[0]
      metadata = self.store.snapshot_metadata(snapshot_id)
      if metadata.get("kind") not in allowed:
        raise KeyError(snapshot_id)
      document = json.loads(self.store.read_snapshot(snapshot_id).iloc[0]["report"])
      return {"snapshot_id": snapshot_id, "kind": metadata["kind"], "report": document}
    except (KeyError, ValueError, IndexError) as error:
      raise AppError("report_not_found", "Research report not found.", 404) from error

  def session_date(self):
    local = self.clock().astimezone(ZoneInfo("Asia/Shanghai"))
    day = pd.Timestamp(local.date())
    calendar = _china_calendar()
    side = "right" if local.time() >= time(15) else "left"
    position = calendar.sessions.searchsorted(day, side=side) - 1
    if position < 0 or day > calendar.last_session:
      raise AppError("calendar_unavailable", "Published session calendar unavailable.", 422)
    return calendar.sessions[position].date()

  def prepare_submission(self, ticker: str, horizon=5, idempotency_key=None, *, priority=10):
    ticker = research_ticker(ticker)
    request = PredictionRequest(ticker=ticker, horizon=horizon)
    if self.settings.max_horizon < 20:
      raise AppError("validation_error", "Research predictions require horizon 20.", 422)
    identity = self.identity()
    if idempotency_key is not None and (
      not idempotency_key.strip() or len(idempotency_key) > 128
    ):
      raise AppError("validation_error", "Invalid idempotency key.", 422)
    payload = {**request.model_dump(), "identity": identity}
    if idempotency_key is None:
      payload.pop("horizon")
      payload["session_date"] = self.session_date().isoformat()
      payload["artifact_revision"] = self.runtime.revision(ticker)
      key = "auto:" + digest(payload)
    else:
      key = "explicit:" + digest({"key": idempotency_key, "identity": identity})
    return {"kind": "prediction", "payload": payload, "idempotency_key": key, "priority": priority}

  def submit(self, ticker: str, horizon=5, idempotency_key=None, *, priority=10,
             parent_job_id=None, lease_seconds=300):
    specification = self.prepare_submission(ticker, horizon, idempotency_key, priority=priority)
    try:
      job_id = self.store.submit_job(
        **specification,
        parent_job_id=parent_job_id, lease_seconds=lease_seconds,
      )
    except ValueError as error:
      raise AppError("idempotency_conflict", "Key already used for another payload.", 409) from error
    return {"job_id": job_id, "status": self.store.get_job(job_id)["status"]}

  def freeze(self, ticker):
    from .pipeline import PersistedMarketProvider

    ticker = research_ticker(ticker)
    provider = getattr(self.market, "provider", None)
    bars_id = None
    if isinstance(provider, PersistedMarketProvider):
      result, bars_id = provider.fetch_with_provenance(ticker, DateSelection(period="5y"))
      history = normalize_frame(ticker, result, include_volume=True)
    else:
      history = self.market.get(ticker, DateSelection(period="5y"), include_volume=True)
    cutoff = self.session_date()
    rows = [
      {"date": item.date.isoformat(), "price": item.price, "volume": item.volume}
      for item in history.observations if item.date <= cutoff
    ][-513:]
    if len(rows) < 33:
      raise AppError("insufficient_history", "At least 32 complete returns are required.", 422)
    validate_prices(np.array([row["price"] for row in rows]))
    dates = [row["date"] for row in rows]
    try:
      sessions = _china_calendar().sessions_in_range(dates[0], dates[-1])
    except Exception as error:
      raise AppError("calendar_unavailable", "Published session calendar unavailable.", 422) from error
    if dates != [session.date().isoformat() for session in sessions]:
      raise AppError("non_continuous_sessions", "Context contains missing trading sessions.", 422)
    instrument_type = "index" if ticker in INDEX_NAMES else "stock"
    adjustment = (
      "unadjusted" if instrument_type == "index"
      else "qfq" if history.source.startswith("akshare") else "unknown"
    )
    warnings = [
      "Frozen current-vintage prices are not PIT corporate-action data; replay does not restore historical adjustments.",
    ]
    if adjustment == "unknown":
      warnings.append("Price adjustment provenance is unknown for this provider.")
    if dates[-1] != cutoff.isoformat():
      warnings.append("The available history ends before the latest completed session.")
    metadata = {
      "kind": "prices", "ticker": ticker, "origin": dates[-1],
      "name": INDEX_NAMES.get(ticker) or history.name,
      "instrument_type": instrument_type, "source": history.source,
      "price_column": history.price_column, "currency": history.currency,
      "adjustment": adjustment, "adjustment_revision": None,
      "point_in_time": False, "fetched_at": self.clock().isoformat(),
      "warnings": warnings, "schema_version": 2,
      "prediction_identity": self.identity(),
      "artifact_revision": self.runtime.revision(ticker),
    }
    if bars_id is not None:
      prices, candidate = price_snapshot(self.store, bars_id, ticker, dates[-1])
      expected = pd.to_datetime(dates)
      if not expected.isin(prices.index).all() or not np.array_equal(
        prices.reindex(expected).to_numpy(float), [row["price"] for row in rows],
      ):
        raise AppError("snapshot_mismatch", "Frozen prices differ from persisted input.", 409)
      metadata.update(bars_snapshot_id=bars_id, dataset_ids={"bars": bars_id},
                      adjustment=candidate["adjustment"], source=candidate["source"],
                      adjustment_revision=candidate.get("adjustment_revision"),
                      source_version=candidate.get("source_version"))
    metadata["model_provenance"] = self.runtime.training_provenance(ticker, dates[-1])
    feature_refs = {}
    for horizon, model in metadata["model_provenance"]["lightgbm"].items():
      if model["feature_schema"]:
        feature = latest_snapshot(self.store, "features", ticker=ticker, origin=dates[-1],
          feature_schema=model["feature_schema"], trusted=True, point_in_time=True)
        if feature:
          feature_refs[horizon] = feature[0]
    metadata["inference_refs"] = {"bars": metadata.get("bars_snapshot_id"), "features": feature_refs}
    snapshot_id = self.store.save_snapshot(pd.DataFrame(rows), metadata)
    frozen = self.store.read_snapshot(snapshot_id)
    return snapshot_id, frozen, metadata, log_returns(frozen["price"].to_numpy(dtype=float))

  def execute(self, job):
    def checkpoint():
      if not self.store.job_active(job["id"]):
        raise AppError("job_inactive", "Job cancelled or lease expired.", 409)

    checkpoint()
    payload = job["payload"]
    ticker = research_ticker(payload["ticker"])
    if payload.get("identity") != self.identity():
      raise AppError("configuration_changed", "Worker configuration differs from submission.", 409)
    revision = self.runtime.revision(ticker)
    if payload.get("artifact_revision", revision) != revision:
      raise AppError("artifact_changed", "Artifacts changed; submit a new prediction.", 409)
    snapshot_id, frozen, metadata, returns = self.freeze(ticker)
    checkpoint()
    origin = pd.Timestamp(metadata["origin"]).date()
    dates = future_business_days(origin, 20, ticker)
    horizons = [{"horizon": value, "target_date": dates[value - 1]} for value in (1, 5, 20)]
    warnings = list(metadata["warnings"])
    path = []
    try:
      context = np.ascontiguousarray(
        returns[-self.identity()["context_length"]:], dtype=np.float32,
      )
      output = self.model.predict(context, 20)
      output = validate_output(output.point, output.quantiles, 20)
      with np.errstate(over="ignore", invalid="ignore"):
        cumulative = np.expm1(np.cumsum(output.point))
      if not np.isfinite(cumulative).all() or (cumulative <= -1).any():
        raise AppError("model_output_invalid", "Invalid cumulative return path.", 502)
      path = [
        {"date": target, "cumulative_return": float(value)}
        for target, value in zip(dates, cumulative, strict=True)
      ]
      for row in horizons:
        row["timesfm_return"] = float(cumulative[row["horizon"] - 1])
      artifact_id = getattr(self.model, "artifact_id", None) or None
      timesfm = ComponentState(status="ready", artifact_id=artifact_id)
      if artifact_id is None:
        warnings.append("TimesFM checkpoint name is recorded; an immutable weight revision is not pinned.")
    except Exception as error:
      timesfm = ComponentState(status="unavailable", reason=safe_code(error))
    checkpoint()
    context = {"snapshot_id": snapshot_id}
    lightgbm, directions = self.runtime.lightgbm(
      ticker, metadata["origin"], metadata["instrument_type"], context=context,
    )
    checkpoint()
    garch, risks = self.runtime.garch(ticker, metadata["origin"], returns, context=context)
    checkpoint()
    if self.runtime.revision(ticker) != revision:
      raise AppError("artifact_changed", "Artifacts changed during prediction; resubmit.", 409)
    for row in horizons:
      prediction = directions.get(row["horizon"])
      if prediction is not None:
        row["lightgbm_return"], row["up_probability"] = prediction
      row["volatility"] = risks.get(row["horizon"])
    components = {"timesfm": timesfm, "lightgbm": lightgbm, "garch": garch}
    status = "succeeded" if all(
      item.status in {"ready", "not_supported"} for item in components.values()
    ) else "partial"
    return PredictionBundle(
      bundle_id=job["id"], ticker=ticker, name=metadata["name"],
      instrument_type=metadata["instrument_type"], origin=origin,
      issued_at=self.clock(), snapshot_id=snapshot_id, status=status,
      horizons=horizons, path=path,
      history=frozen[["date", "price"]].to_dict("records"),
      components=components, warnings=warnings,
    ).model_dump(mode="json")