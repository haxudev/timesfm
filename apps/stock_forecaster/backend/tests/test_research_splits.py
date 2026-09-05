import pandas as pd
import pytest


def test_purge_uses_label_end_and_keeps_date_groups_together():
  from stock_forecaster.research.splits import purged_split

  frame = pd.DataFrame(
    {
      "ticker": ["A", "B", "C", "A", "A", "A", "A"],
      "origin": [
        "2026-01-01",
        "2026-01-01",
        "2026-01-02",
        "2026-01-04",
        "2026-01-06",
        "2026-01-08",
        "2026-01-09",
      ],
      "target_date": [
        "2026-01-03",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
        "2026-01-07",
        "2026-01-09",
        "2026-01-11",
      ],
    }
  )
  result = purged_split(frame, "2026-01-03", "2026-01-05", "2026-01-07", "2026-01-10")
  assert {key: list(value.index) for key, value in result.items()} == {
    "train": [0, 1],
    "validation": [3],
    "calibration": [4],
    "test": [5],
  }
  assert frame.origin.dtype == object or isinstance(frame.origin.dtype, pd.StringDtype)


@pytest.mark.parametrize("target", ["2025-12-31", "2026-01-01"])
def test_rejects_labels_ending_at_or_before_origin(target):
  from stock_forecaster.research.splits import purged_split

  frame = pd.DataFrame({"origin": ["2026-01-01"], "target_date": [target]})
  with pytest.raises(ValueError, match="invalid_label_interval"):
    purged_split(frame, "2026-01-03", "2026-01-05", "2026-01-07", "2026-01-10")


def test_unknown_label_end_excluded_and_unordered_bounds_rejected():
  from stock_forecaster.research.splits import purged_split

  frame = pd.DataFrame({"origin": ["2026-01-01"], "target_date": [None]})
  result = purged_split(frame, "2026-01-03", "2026-01-05", "2026-01-07", "2026-01-10")
  assert all(part.empty for part in result.values())
  with pytest.raises(ValueError, match="invalid_split_bounds"):
    purged_split(frame, "2026-01-05", "2026-01-05", "2026-01-07", "2026-01-10")
