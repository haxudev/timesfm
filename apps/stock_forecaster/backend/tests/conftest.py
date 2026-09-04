from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stock_forecaster.config import Settings
from stock_forecaster.main import create_app
from stock_forecaster.market_data import ProviderResult
from stock_forecaster.model import ModelManager, ModelOutput


@dataclass
class FakeProvider:
  result: ProviderResult
  calls: int = 0

  def fetch(self, ticker, selection):
    self.calls += 1
    return self.result


class FakeAdapter:
  def __init__(self):
    self.contexts: list[np.ndarray] = []

  def predict(self, context: np.ndarray, horizon: int) -> ModelOutput:
    self.contexts.append(context.copy())
    point = np.full(horizon, 0.01)
    quantiles = np.tile(np.linspace(-0.02, 0.02, 9), (horizon, 1))
    return ModelOutput(point, quantiles)


@pytest.fixture
def market_frame() -> pd.DataFrame:
  dates = pd.bdate_range("2024-01-01", periods=80)
  prices = 100 * np.exp(np.linspace(0, 0.3, len(dates)))
  return pd.DataFrame(
    {
      "Adj Close": prices,
      "Close": prices + 10,
      "Volume": np.arange(len(dates)) + 1000,
    },
    index=dates,
  )


@pytest.fixture
def app_parts(market_frame):
  provider = FakeProvider(ProviderResult(market_frame, "USD"))
  adapter = FakeAdapter()
  loads: list[str] = []

  def factory(checkpoint: str, device: str):
    loads.append(device)
    return adapter

  manager = ModelManager("trusted/checkpoint", factory=factory)
  settings = Settings(
    cache_ttl_seconds=60,
    default_context_length=32,
    device="cpu",
  )
  app = create_app(settings, provider, manager)
  return TestClient(app), provider, adapter, loads
