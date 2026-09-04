import numpy as np

from .errors import AppError


def validate_prices(values: np.ndarray) -> np.ndarray:
  prices = np.asarray(values, dtype=np.float64)
  if prices.ndim != 1 or prices.size == 0:
    raise AppError("invalid_prices", "Prices must be a non-empty series.", 422)
  if not np.isfinite(prices).all():
    raise AppError("non_finite_prices", "Prices must all be finite.", 422)
  if (prices <= 0).any():
    raise AppError("non_positive_prices", "Prices must all be positive.", 422)
  return prices


def log_returns(values: np.ndarray) -> np.ndarray:
  prices = validate_prices(values)
  returns = np.diff(np.log(prices)).astype(np.float32)
  if not np.isfinite(returns).all():
    raise AppError("invalid_returns", "Log returns must all be finite.", 422)
  return np.ascontiguousarray(returns)


def returns_to_prices(last_price: float, returns: np.ndarray) -> np.ndarray:
  if not np.isfinite(last_price) or last_price <= 0:
    raise AppError("invalid_last_price", "Last price must be positive and finite.", 422)
  values = np.asarray(returns, dtype=np.float64)
  if not np.isfinite(values).all():
    raise AppError("invalid_returns", "Forecast returns must all be finite.", 422)
  result = last_price * np.exp(np.cumsum(values, axis=0))
  if not np.isfinite(result).all():
    raise AppError("invalid_price_path", "Forecast price path is not finite.", 500)
  return result
