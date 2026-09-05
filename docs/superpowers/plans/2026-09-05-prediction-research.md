# Multi-model Prediction Research Implementation

**Goal:** A prediction-first A-share application with TimesFM, LightGBM return and calibrated probability models, independent GARCH volatility, reproducible inputs, and daily prospective evaluation.

**Architecture:** WSL2 Docker Compose with FastAPI, a single research worker, Parquet snapshots queried with DuckDB, and transactional SQLite jobs. The frontend is a single-stock prediction workspace, not a database dashboard. Preserve the existing v1 API and all pre-existing workspace changes.

**Global Constraints**

- Full mainland A-share daily universe, including Shanghai, Shenzhen and Beijing; exclusions and missing data must be counted.
- Prediction horizons are 1, 5 and 20 exchange sessions, not observed quote rows.
- Use free data first. Missing historical industry or adjustment provenance blocks full LightGBM acceptance; never backfill current classifications as historical truth.
- LightGBM: one shared return regressor and one calibrated classifier per horizon. Event: cumulative simple return > 0.
- GARCH: zero-mean GARCH(1,1), Student-t innovations, explicit offline fitting, decimal return variance and cumulative volatility.
- Fit/calibrate only in background jobs; HTTP prediction uses published artifacts only.
- Forecast output, probabilities, and volatility are distinct types. Do not accumulate marginal quantiles into a claimed joint price interval.
- Keep backend host port 8010 and frontend 5173; do not occupy host 8000/8001.
- No branches or commits without explicit authorization. Preserve current dirty worktree.
- No deployment outside this machine, paid data purchase, live trading, or profitability claims.

## Stages And Acceptance

- [x] Local baseline: 83 backend tests before implementation; WSL Docker available (16 CPUs, approximately 14 GiB assigned).
- [x] Immutable Parquet snapshots, metadata integrity and transactional durable jobs. Dedicated real-I/O tests pass.
- [x] Origin-bounded features, exchange-session labels and purged splits. Historical industry is mandatory.
- [x] LightGBM regression/classification, held-out sigmoid calibration, native model artifacts and load integrity tests.
- [x] GARCH fit, fixed-parameter forecasts, units/variance tests and native JSON artifacts.
- [x] v2 prediction/job API, snapshot-first single-stock execution, internal TimesFM reuse, worker and offline replay.
- [x] Prediction-first UI, honest null/partial states, one chart, retained advanced v1 view. Desktop/mobile browser fixtures tested.
- [x] Live limited source probe: current universe 5,556 stocks; two stocks/two indices downloaded without missing OHLCV/amount fields. Historical industry publication remains unverified.
- [x] Offline dataset/one-fold training orchestration and immutable native artifacts. Verified-data gate exercised; no real LightGBM training on unverified inputs.
- [x] Due-forecast scoring with baselines, optional daily/weekly scheduling, durable failure reports and bounded recovery. Full-market overnight execution is not yet accepted.
- [x] WSL container build, real TimesFM/GARCH single-stock smoke and offline backup/restore including the fitted GARCH artifact.
- [x] Scoped reviews and regression fixes for provenance, cancellation, leases, atomic publication, corruption and missing-session coverage.
- [ ] Full historical PIT data acceptance, three-fold model promotion evidence, grouped daily inference and full-market capacity acceptance.

## Verified Running Build

- Application: `http://localhost:5174`; API: `http://localhost:8012`.
- Compose project: `timesfm-research`; existing host services on 5173/8010 and 8000/8001 were not stopped or replaced.
- Linux backend: 388 tests passed; Ruff passed after normalizing copied Python file permissions.
- Frontend: 66 unit tests and 24 four-viewport browser fixture checks passed; TypeScript/build/lint passed. Actual container page also checked at 1440/390 widths with no page overflow or JavaScript errors.
- Official TimesFM checkpoint pinned to revision `43046b85ec22d584a13f8098c2ed39c889e129c2`, carried into stored prediction identity. A bounded authenticated batch endpoint accepts up to eight contexts.
- Real prediction `ac3d3e3b6bd842f0a50a80468b6a4ed8` for `600519.SS`, origin `2026-09-04`, returned TimesFM and GARCH ready, LightGBM not ready. No probability was fabricated.
- A separate network-disabled container replayed that prediction and loaded the backed-up GARCH model/input snapshot successfully.
- Warm CPU batch smoke, repeated same real 512-point context and horizon 20: batches 1/2/4/8 took 0.566/0.482/0.635/0.873 seconds. This is not a distinct-stock full-market throughput benchmark or end-to-end SLA.
- Live data and screenshots/backup are ignored local outputs, not committed source. Data-source licensing and historical publication semantics are not inferred from successful downloads.
- Default schedule stays opt-in. Do not label the entire plan complete while the unchecked real-data/model-validation/capacity gates remain.

## Execution Order

1. Test a behavior that would fail on the missing implementation, observe failure, implement the smallest slice and immediately rerun the focused test.
2. Establish actual data capabilities before bulk fetching or training. Retain raw source fields and retrieval time; source effective dates are not automatically publication dates.
3. Store input snapshots before prediction. Preserve old snapshots and model versions when vendors revise historical prices.
4. Build single-stock predictions and the UI before scheduling a full market. Real model absence must remain visible.
5. Train using chronological train/validation/calibration/test segments and label-end purging. No random row split and no calibration on the final test set.
6. Run model comparisons on common eligible samples, while reporting each model's coverage separately.
7. Freeze predictions before their first target session. Late/replayed predictions never enter prospective results.
8. Add automatic full-market execution only after measuring data-source request budgets and model throughput.

## Verification

Run from the repository root (inside the test container use `/repo`, not `/app`):

```sh
python -m pytest -c apps/stock_forecaster/backend/pyproject.toml apps/stock_forecaster/backend/tests -q
python -m ruff check apps/stock_forecaster/backend
npm --prefix apps/stock_forecaster/frontend run --silent test -- --run
npm --prefix apps/stock_forecaster/frontend run --silent build
npm --prefix apps/stock_forecaster/frontend run --silent lint
npm --prefix apps/stock_forecaster/frontend run --silent test:e2e
```

Normal tests use controlled provider/model boundaries, real storage, and small native model training fixtures. They do not prove real-market performance. Browser fixtures verify 360/390/1280/1440 widths, nonempty Plotly charts, interactions and null/error handling.

## Evidence Boundaries

- Engineering correctness, real-data completeness, out-of-sample model quality, and prospective maturity are separate acceptance statuses.
- TimesFM generic pretraining cannot by itself prove historical data independence.
- Current-vintage adjusted price snapshots are reproducible but do not reconstruct historical corporate-action information.
- Twenty-session prospective evaluation cannot be completed on the first day.
- Validation reports must retain the snapshot, code/configuration identity, model artifact, feature schema, training cutoff, calibration cutoff, issue time, and label dates.
- Do not claim a model is validated merely because its library imports, its unit tests pass, or its predictions render.