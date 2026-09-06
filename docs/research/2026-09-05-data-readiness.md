# Historical Data Readiness: 2026-09-05

## Decision

There is enough downloaded history to train a bounded price/index LightGBM
experiment. There is not enough evidence to approve a strict historical PIT
backtest or deploy a predictive model. All six native LightGBM heads trained,
reloaded and evaluated, but none beat zero-return MAE on the held-out period.
Keep these artifacts as candidates only.

## Scope And Collection

- Local store: `.local/data-readiness-20260905`, independent of running Docker volumes.
- Source: AKShare `stock_zh_a_hist_tx`; raw and current-vintage qfq stocks, raw indices.
- Selection: first 30 current stock symbols, all SZ; 4 indices. This is deliberately
  bounded but not randomized, representative, historical-universe or full-market coverage.
- Requested history: 2021-09-01 through 2026-09-04, 1,214 exchange sessions.
- Current universe response: 5,556 symbols. All 34 requested price histories downloaded.
- Industry endpoint exhausted bounded transient retries; collection report is partial.
  Historical industry publication semantics would remain unverified even if it succeeded.
- 65 successful SDK response snapshots persisted. No HTTP bodies or failed responses
  are claimed to be archived. Full window payloads are immutable; old vintages are not overwritten.
- At verification: 79 Parquet files, 5,595,368 bytes. This is this small run only,
  not an estimate of full-market storage or peak memory.
- Price dataset: 36,374 stock rows plus 1,214 CSI 300 benchmark rows.
- 37 missing stock sessions within observed lifespans; 230 excluded feature windows;
  35,589 usable feature rows; 108,097 mature labels before joining feature eligibility.
  Label counts combine three horizons and are not independent observations.
- No separate adjustment-factor events, historical ST/delisting archive, licensed
  PIT financials, or independent-provider reconciliation were added in this run.

## Reproducible Identifiers

| Object | SHA-256 snapshot/artifact ID |
| --- | --- |
| Collection report | `5e9d9356289420bbfefd650a6c52479d779bbc694f6b8f202b26a56bc7ad7bf5` |
| Dataset manifest | `04743d385c46b5d8495b6407bb4d7b7ac744cf0135311259d4846163b4c79147` |
| Stock bars | `ffec9872b2c88ba9a5b4029ad142f51f8dff3d4137e0abc1717dc9e075f8ec53` |
| Benchmark | `90cab144e0d3112096134a022fb6aa6e02071d015c265ee1fd5b82c3268f210b` |
| Readiness report | `df776e0d3b3dbb12bdbdb3eab661a7c09dd2863502f1338d013b762123177714` |
| Training report | `1df82bf2455abb50b3f00ec2fed14c7ef5badf3de514a31325b605e30d0e8f97` |
| LightGBM 1 session | `33cd52fa85bf5b5ff73e3a7be1d376b121624e58811b1f6032bc1d8f7c6c5ee0` |
| LightGBM 5 sessions | `8b730ad94999955a5ef8475d4990d4b410bbcdcfe2421d86c947cde429a16e5e` |
| LightGBM 20 sessions | `12ce826c974010e2d51c8b4e400ae722084dc0a0ae15c5fc7724dde17d02d15c` |
| GARCH 000001.SZ | `c1e9a4e5a9eba424980266507765aa097571197fa8a049dda249428c0bc3834c` |

Snapshots are readable through `ResearchStore.read_snapshot(ID)`; reports have a
single JSON `report` cell. Native models are under `models/artifacts/ID` in the store.
These local datasets/artifacts are ignored by Git; this document preserves the references.

## Experiment Definition

- Mode `historical_research`, feature specification `price_index_v1`, candidate only.
- Features: stock and benchmark momentum at 1/5/20 sessions, daily log-return
  volatility at 5/20 sessions, 20-session volume/amount ratios and price amplitude.
- No industry feature substitution, fabricated category or trusted/PIT flag override.
- One shared stock-pool regressor and classifier per horizon; sigmoid calibration
  fitted only on its separate calibration partition; 100 maximum estimators, 2 threads.
- Split ends: train 2024-06-06, validation 2025-03-10, calibration 2025-12-04,
  test 2026-09-04. All labels purged at the corresponding partition's end.
- Test origins begin 2025-12-05. No hyperparameter tuning on this test set was performed.

| Horizon | Train rows | Validation rows | Calibration rows | Test rows | Test origin dates |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 19,273 | 5,377 | 5,430 | 5,380 | 182 |
| 5 | 19,137 | 5,249 | 5,310 | 5,248 | 178 |
| 20 | 18,627 | 4,780 | 4,860 | 4,754 | 163 |

## Held-Out Results

MAE below is decimal simple-return error. Brier is squared probability error;
lower is better. The probability baseline is the corresponding training split's
up frequency, not a value estimated on test data.

| Horizon | Return MAE | Zero-return MAE | Brier | Training-prior Brier | AUC |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.02112618 | 0.02101135 | 0.24819163 | 0.24874028 | 0.55391278 |
| 5 | 0.04825484 | 0.04815591 | 0.25090775 | 0.24767049 | 0.51201639 |
| 20 | 0.09899847 | 0.09768975 | 0.26215219 | 0.24653997 | 0.54044591 |

The one-session Brier improvement is small and has no significance assessment.
Five- and twenty-session probability errors are worse than the baseline. Positive
AUC alone does not establish calibrated probabilities, tradable returns or stability.
The same-date cross-section and overlapping horizon labels are correlated; thousands
of rows cannot be treated as thousands of independent trials.

## Other Models

- TimesFM: 29/30 symbols have at least 33 consecutive positive prices with available
  activity fields positive, allowing 32 return context points. This is input readiness
  for the existing pretrained model, not a new neural training/fine-tuning run.
- GARCH: 27/30 symbols meet 253 consecutive price points. Actual 000001.SZ fit on the
  latest 512 returns succeeded and the native artifact reloaded. Other symbols were
  not fitted in this run; length eligibility does not guarantee convergence.
- 000016.SZ lacks a latest-session price; 000008.SZ and 000010.SZ have only 39 and 88
  consecutive prices respectively at the dataset's end. No gap was filled.
- Context checks are intentionally univariate close/activity checks. They do not
  certify multivariate OHLC feature completeness or future risk accuracy.

## Acceptance And Remaining Gates

- Final full backend tests: 404 passed, 24 existing Pandas categorical deprecation warnings.
- Ruff and edited-code editor checks passed. No frontend files changed.
- Tests cover strict/default rejection, candidate-only enforcement, native six-head
  reload, immutable original observations, missing/zero-activity windows, source
  basis consistency, split counts and one-class refusal.
- No online LightGBM reference, trusted inference feature snapshot, service restart,
  paid account, model promotion or schedule enablement was performed.
- `strict_pit.ready=false`: historical source revisions, point-in-time universe,
  historical industry/publication evidence and future-proof decision cutoff handling
  still require work. Strict existing cutoff policy was not changed in this slice.
- Before a promotion decision: representative historical stock universe including
  delistings, corporate actions and source reconciliation, explicit decision/execution
  timestamps, multiple walk-forward folds and costs/trading constraints are required.
- Current history is suitable for experiments. It is not evidence that collecting more
  rows alone will improve forecasts; feature choice and out-of-sample validation remain necessary.