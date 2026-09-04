import numpy as np
import pytest

from stock_forecaster.errors import AppError
from stock_forecaster.features import log_returns, returns_to_prices


def test_log_returns_and_price_conversion():
  prices = np.array([100.0, 110.0, 99.0])
  returns = log_returns(prices)
  assert returns.dtype == np.float32
  np.testing.assert_allclose(
    returns,
    [np.log(1.1), np.log(0.9)],
    rtol=1e-6,
  )
  np.testing.assert_allclose(returns_to_prices(100, returns), [110, 99], rtol=1e-6)


@pytest.mark.parametrize("prices", [[1, 0, 2], [1, -1, 2], [1, np.inf, 2]])
def test_log_returns_reject_invalid_prices(prices):
  with pytest.raises(AppError):
    log_returns(np.array(prices))


def test_price_conversion_rejects_non_finite_returns():
  with pytest.raises(AppError):
    returns_to_prices(100, np.array([np.nan]))
