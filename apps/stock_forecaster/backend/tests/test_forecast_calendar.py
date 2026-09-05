from datetime import date

import pandas as pd
import pytest

from stock_forecaster.errors import AppError
from stock_forecaster.forecasting import future_business_days


def test_chinese_forecast_skips_mid_autumn_and_national_day():
  assert future_business_days(date(2026, 9, 24), 5, "000001.SS") == [
    date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
    date(2026, 10, 8), date(2026, 10, 9),
  ]


@pytest.mark.parametrize("horizon", [1, 3, 5, 10, 20, 60])
def test_daily_horizon_presets_return_exact_sessions(horizon):
  dates = future_business_days(date(2026, 9, 4), horizon, "600519")
  assert len(dates) == horizon
  assert dates[0] == date(2026, 9, 7)
  assert len(set(dates)) == horizon
  assert all(value.weekday() < 5 for value in dates)


def test_unknown_calendar_year_is_not_guessed():
  with pytest.raises(AppError) as caught:
    future_business_days(date(2099, 1, 1), 5, "399006.SZ")
  assert caught.value.code == "calendar_unavailable"


def test_overseas_weekday_behavior_is_preserved():
  assert future_business_days(date(2026, 9, 24), 1, "SPY") == [date(2026, 9, 25)]


def test_forecast_rejects_crossing_calendar_end_before_loading_model(app_parts):
  client, provider, adapter, loads = app_parts
  provider.result.frame.index = pd.bdate_range(
    end="2026-12-31", periods=len(provider.result.frame),
  )
  response = client.post(
    "/api/v1/forecasts",
    json={"ticker": "000001.SS", "horizon": 5, "context_length": 32},
  )
  assert response.status_code == 422
  assert response.json()["error"]["code"] == "calendar_unavailable"
  assert loads == []
  assert adapter.contexts == []