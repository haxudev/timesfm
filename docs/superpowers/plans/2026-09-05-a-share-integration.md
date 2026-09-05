# A-share Integration Implementation Plan

**Goal:** Connect Tencent/Sina quotes and AKShare daily history to the existing
TimesFM stock dashboard, then run the application locally.

**Architecture:** Canonical A-share symbols route to AKShare; other symbols keep
yfinance. A separate quote endpoint uses Tencent first and Sina on failure.
Historical adjusted daily prices remain the only model input. Quotes never
enter the training context or backtest windows.

**Tech Stack:** Existing FastAPI/Pydantic, pandas, TimesFM 3, React, TanStack
Query, Plotly, pytest and Vitest; add AKShare and runtime HTTPX.

## Approved Design

The user approved direct implementation of Tencent primary, Sina fallback,
AKShare history, overseas-symbol compatibility, and a default A-share view.
Poll quotes every 10 seconds only while visible. Display quote source/time,
five bid/ask levels, and nullable Tencent valuation fields. Surface errors
without fabricated data. Use bounded upstream timeouts and quote caching.
Preserve the model's research-only and non-commercial weight restrictions.

## Execution

- [x] Canonical symbols and daily history: extend `test_market_data.py`, run
  pytest, implement symbol routing and AKShare adjusted history conversion.
  Verify range boundaries, CNY, volume units, and overseas compatibility.
- [x] Real-time quotes: add parser/provider tests, fail them, implement typed
  quote schema, GBK parsing, validation, Tencent-to-Sina fallback, short cache
  and API endpoint. Test malformed responses and exhausted fallbacks.
- [x] Dashboard: extend UI tests, add quote request and compact quote/order-book
  panel using existing styling, default to an A-share symbol. Preserve forecast,
  backtest, loading/error states and CSV export. Run tests, typecheck and build.
- [x] Integration: document sources/units/limits, run all app tests and lint,
  check machine capacity before loading weights, verify live quotes and history,
  run a real TimesFM forecast, start backend/frontend, inspect desktop/mobile.

No commits, branches, or unrelated generated-image changes are included.

## Verification

- Backend: 62 offline tests passed; Ruff and compileall passed.
- Frontend: 10 tests passed; ESLint and TypeScript/Vite production build passed.
  The existing Plotly bundle still produces Vite's large-chunk warning.
- Live Tencent and Sina quotes succeeded; AKShare supplied 484 adjusted daily
  observations for 600519.SS through 2026-09-04.
- TimesFM 3.0 weights downloaded and cached; CPU forecasts succeeded with
  128 and default 512 requested context values. Two-window live backtest passed.
- Browser form submitted to port 8010 and returned five forecast steps. All
  three Plotly charts rendered data; desktop and narrow layouts had no overflow
  after responsive chart resizing settled.
- User requested avoiding 8001. Backend runs on 8010, frontend on 5173;
  the ignored local Vite configuration retains the new backend URL.