# Historical Data Readiness Implementation Plan

> **For agentic workers:** Execute inline with test-driven changes and focused checks.

**Goal:** Strengthen free historical data retention and measure whether actual observations support bounded model experiments.

**Architecture:** Keep strict PIT training as the default. Add an explicit candidate-only historical research mode using a separately identified price/index feature specification, plus a reproducible collection-to-dataset readiness report. Never reclassify backfilled observations as historical publication evidence.

**Tech Stack:** Existing Python, AKShare, pandas, Parquet, DuckDB, SQLite, LightGBM and arch.

## Global Constraints

- No paid subscriptions, credentials, automatic model promotion, or replacement of running services.
- Preserve existing industry-based model specification and strict training defaults.
- Free historical backfills retain source, collection time, original snapshots and limitations.
- Real runs use bounded collections and existing training budgets; partial samples are not full-market validation.
- No commits or removal of unrelated user changes.

## Task 1: Isolated Historical Research Training

Files: backend research/training.py, features.py, gradient_boosting.py as needed; tests/test_research_training.py and feature tests.

- [x] Add failing tests for `load_dataset(mode='historical_research', feature_set='price_index_v1')`, strict rejection and activation refusal.
- [x] Implement mode validation, immutable provenance, explicit feature schema and candidate-only artifacts.
- [x] Execute real native six-head training on offline fixtures; assert no active references or trusted feature snapshots are created.

## Task 2: Collection Provenance and Readiness

Files: backend research/providers.py, pipeline.py, new datasets.py, cli.py and focused tests.

- [x] Preserve downloaded provider frames separately from normalized bars, and add observed/publication timestamp semantics without inventing publication times.
- [x] Assemble only an explicit collection report's snapshots, one benchmark and one adjustment/source basis.
- [x] Report calendar coverage, missing/invalid fields, eligible ticker windows, label maturity and purged partition/class counts for 1/5/20 sessions.
- [x] Expose bounded dataset preparation and readiness commands; persist immutable input references and limitations.
- [x] Test invalid/mixed bases, missing histories, non-PIT status and CLI behavior.

## Task 3: Real Evidence and Documentation

- [x] Collect a bounded multi-stock historical sample and benchmark without submitting predictions or enabling schedules.
- [x] Run readiness checks and candidate-only LightGBM training only if eligible; measure actual rows, sessions, split counts and held-out metrics.
- [x] Check TimesFM context and GARCH history readiness separately from LightGBM training and statistical usefulness.
- [x] Run backend tests/lint, document commands, source coverage, limitations and actual results.

## Execution Evidence

- Candidate dataset: 30 current SZ stocks, 2021-09-01 through 2026-09-04, 36,374 stock bars and 1,214 benchmark bars.
- All six LightGBM heads trained; one real GARCH candidate fitted. No active model references or trusted inference features created.
- Historical research readiness passed, strict PIT readiness failed as intended. No convincing predictive advantage; do not promote.
- Read-only review findings about zero-activity collection counts and strict label safety reproduced with failing tests and repaired. Univariate close/activity context checks intentionally differ from multivariate OHLC feature checks, now explicitly identified in reports.
- Full backend suite after repairs: 404 passed, 24 existing Pandas categorical warnings. See ../../research/2026-09-05-data-readiness.md for reproducible evidence.

## Verification

Use `C:/Users/haxu/AppData/Local/Programs/Python/Python313/python.exe -m pytest -c apps/stock_forecaster/backend/pyproject.toml apps/stock_forecaster/backend/tests/test_research_training.py -q` for the first slice, then focused tests for subsequent slices and the full backend suite. Check Ruff and editor diagnostics. Real-data evidence must include immutable report/dataset identifiers; failure to meet a gate remains a reported blocker, never a metadata override.