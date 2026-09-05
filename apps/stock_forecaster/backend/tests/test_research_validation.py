import math

import numpy as np
import pytest


def test_return_metrics_hand_computed_and_zero_not_up():
  from stock_forecaster.research.validation import return_metrics

  result = return_metrics([0.1, -0.1, 0], [0.0, -0.2, 0.1])
  assert result["n"] == 3
  assert result["mae"] == pytest.approx(0.1)
  assert result["rmse"] == pytest.approx(0.1)
  assert result["direction_accuracy"] == pytest.approx(1 / 3)


def test_probability_metrics_against_independent_expectations():
  from stock_forecaster.research.validation import probability_metrics

  result = probability_metrics([1, 0, 1, 0], [0.8, 0.3, 0.6, 0.1])
  assert result["n"] == 4
  assert result["brier"] == pytest.approx(0.075)
  assert result["log_loss"] == pytest.approx(-math.log(0.8 * 0.7 * 0.6 * 0.9) / 4)
  assert result["auc"] == 1
  assert probability_metrics([1, 1], [0.5, 0.9])["auc"] is None
  assert np.isfinite(probability_metrics([0, 1], [1, 0])["log_loss"])


def test_variance_metrics_and_documented_zero_clamp():
  from stock_forecaster.research.validation import volatility_metrics

  result = volatility_metrics([1, 4], [2, 2])
  assert result["mae"] == 1.5
  assert result["rmse"] == pytest.approx(math.sqrt(2.5))
  assert result["qlike"] == pytest.approx(0.25)
  zero = volatility_metrics([0], [0])
  assert zero["qlike"] == 0
  assert zero["variance_floor"] == 1e-12


@pytest.mark.parametrize(
  "method,actual,predicted",
  [
    ("return_metrics", [], []),
    ("return_metrics", [1, 2], [1]),
    ("return_metrics", [np.nan], [0]),
    ("probability_metrics", [2], [0.5]),
    ("probability_metrics", [0], [1.1]),
    ("volatility_metrics", [-1], [1]),
    ("volatility_metrics", [1], [-1]),
  ],
)
def test_metrics_reject_invalid_input_instead_of_silent_sample_loss(
  method, actual, predicted
):
  from stock_forecaster.research import validation

  with pytest.raises(ValueError):
    getattr(validation, method)(actual, predicted)
