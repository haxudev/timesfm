"""Chronological splits with label-end purging, never randomized by row."""

from datetime import date

import pandas as pd


def purged_split(
  frame: pd.DataFrame,
  train_end: date | str,
  validation_end: date | str,
  calibration_end: date | str,
  test_end: date | str,
) -> dict[str, pd.DataFrame]:
  """Split on inclusive end dates; subsequent origins must be strictly later.

  Drop labels ending after their partition's end, or with unknown target_date.
  Reject known target_date <= origin. No input mutation or label imputation.
  All horizons are purged using their actual target_date, not a fixed row gap.
  """
  bounds = pd.DatetimeIndex([train_end, validation_end, calibration_end, test_end])
  if bounds.hasnans or not bounds.is_unique or not bounds.is_monotonic_increasing:
    raise ValueError("invalid_split_bounds: ends must be strictly increasing")
  if not {"origin", "target_date"}.issubset(frame.columns):
    raise ValueError("missing_split_columns: origin/target_date required")
  origins = pd.to_datetime(frame["origin"])
  targets = pd.to_datetime(frame["target_date"])
  if origins.isna().any() or (targets.notna() & (targets <= origins)).any():
    raise ValueError("invalid_label_interval: target_date must be after origin")
  result = {}
  previous = None
  for name, end in zip(("train", "validation", "calibration", "test"), bounds):
    eligible = (origins <= end) & (targets <= end) & targets.notna()
    if previous is not None:
      eligible &= origins > previous
    result[name] = frame.loc[eligible].copy()
    previous = end
  return result
