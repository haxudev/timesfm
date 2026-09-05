from __future__ import annotations

from datetime import date
from functools import lru_cache

import exchange_calendars
import numpy as np
import pandas as pd

from .a_share import a_share_symbol
from .errors import AppError
from .features import log_returns, returns_to_prices, validate_prices
from .market_data import MarketDataService
from .model import ModelManager, ModelOutput
from .schemas import ForecastPoint, ForecastRequest, ForecastResponse

_QUANTILE_NAMES = [f"q0.{index}" for index in range(1, 10)]
_MODEL_MAX_CONTEXT = 15360


@lru_cache(maxsize=1)
def _china_calendar():
  return exchange_calendars.get_calendar("XSHG", start="1990-12-19")


def future_business_days(
  last_date: date, horizon: int, ticker: str = "SPY",
) -> list[date]:
  if a_share_symbol(ticker) is None:
    return [value.date() for value in pd.bdate_range(last_date, periods=horizon + 1)[1:]]
  try:
    calendar = _china_calendar()
    start = calendar.sessions.searchsorted(pd.Timestamp(last_date), side="right")
    sessions = calendar.sessions[start : start + horizon]
    if last_date < calendar.first_session.date() or len(sessions) != horizon:
      raise ValueError("Outside published trading calendar")
    return [value.date() for value in sessions]
  except Exception as error:
    raise AppError(
      "calendar_unavailable",
      "预测日期超出已发布的中国交易日历范围，请缩短预测周期或更新交易日历。",
      422,
    ) from error


def _price_output_to_returns(output: ModelOutput, last_price: float) -> ModelOutput:
  point_prices = validate_prices(output.point)
  quantile_prices = np.asarray(output.quantiles, dtype=np.float64)
  if (quantile_prices <= 0).any() or not np.isfinite(quantile_prices).all():
    raise AppError(
      "model_output_invalid",
      "Price forecasts must be positive and finite.",
      502,
    )
  point_previous = np.concatenate(([last_price], point_prices[:-1]))
  quantile_previous = np.vstack(
    [np.full((1, 9), last_price), quantile_prices[:-1]]
  )
  return ModelOutput(
    point=np.log(point_prices / point_previous),
    quantiles=np.log(quantile_prices / quantile_previous),
  )


class ForecastService:
  def __init__(self, market: MarketDataService, model: ModelManager) -> None:
    self.market = market
    self.model = model

  def run(self, request: ForecastRequest) -> ForecastResponse:
    history = self.market.get(request.ticker, request, include_volume=True)
    dates = future_business_days(history.end, request.horizon, history.ticker)
    prices = validate_prices(
      np.array([observation.price for observation in history.observations])
    )
    target = log_returns(prices) if request.target == "log_return" else prices.astype(
      np.float32
    )
    if target.size < 32:
      raise AppError(
        "insufficient_history",
        "At least 32 valid target observations are required.",
        422,
      )
    effective_context = min(request.context_length, _MODEL_MAX_CONTEXT)
    context = np.ascontiguousarray(target[-effective_context:], dtype=np.float32)
    raw_output, device, device_warning = self.model.predict(
      context,
      request.horizon,
      request.device,
    )
    output = (
      raw_output
      if request.target == "log_return"
      else _price_output_to_returns(raw_output, float(prices[-1]))
    )
    point_prices = returns_to_prices(float(prices[-1]), output.point)
    quantile_prices = returns_to_prices(float(prices[-1]), output.quantiles)
    points = [
      ForecastPoint(
        date=dates[index],
        point_return=float(output.point[index]),
        quantiles={
          name: float(output.quantiles[index, quantile])
          for quantile, name in enumerate(_QUANTILE_NAMES)
        },
        point_price=float(point_prices[index]),
        lower_price=float(quantile_prices[index, 0]),
        upper_price=float(quantile_prices[index, 8]),
      )
      for index in range(request.horizon)
    ]
    warnings = [
      (
        "预测日期按中国市场交易日历生成，遇休市日顺延。"
        if a_share_symbol(history.ticker)
        else "海外市场预测日期暂按工作日生成，可能未排除当地休市日。"
      ),
      (
        "逐日收益率分位数累积形成近似价格路径，不代表整条路径的联合置信区间。"
      ),
    ]
    if device_warning:
      warnings.append(device_warning)
    if request.target == "price":
      warnings.append("直接预测价格或点位为实验功能。")
    if min(request.context_length, target.size) > _MODEL_MAX_CONTEXT:
      warnings.append(
        "TimesFM 3 最多使用 15,360 条上下文，本次已截断超出部分。"
      )
    return ForecastResponse(
      ticker=history.ticker,
      target=request.target,
      history=history,
      context={
        "requested_length": request.context_length,
        "used_length": int(context.size),
        "observation_count": history.count,
        "origin": history.end.isoformat(),
      },
      model={
        "checkpoint": self.model.checkpoint,
        "device": device,
        "state": self.model.state,
      },
      forecasts=points,
      warnings=warnings,
      summary={
        "latest_price": float(prices[-1]),
        "predicted_final_price": float(point_prices[-1]),
        "cumulative_return": float(np.exp(output.point.sum()) - 1),
        "final_lower_price": float(quantile_prices[-1, 0]),
        "final_upper_price": float(quantile_prices[-1, 8]),
        "horizon": request.horizon,
      },
    )
