# Chinese UI, Indices and Forecast Horizons

**Goal:** Fully localize the dashboard, support six mainland indices and expose
daily forecast horizons for ultra-short, short and medium-term research.

**Approved scope:** The user approved daily sampling, not intraday bars.
Keep backend8010 and frontend5173; do not touch8000/8001. Preserve existing
stock history, quotes, model interface and chronological backtesting.

## Tasks

- [x] Add six index identities and unadjusted index history. Distinguish
  sh000001 from bare000001. Test all six symbols and stock compatibility.
- [x] Add index snapshot semantics without fabricated order books or stock
  valuations. Test Tencent and Sina index payloads against real formats.
- [x] Use an exchange calendar for Chinese forecast dates. Test1/5/10/20/60
  horizons and holiday boundaries; fail clearly outside supported calendar years.
- [x] Localize all UI labels, chart controls/tooltips, warnings/errors,
  backtests, document title and CSV. Add index/stock modes and1/3/5/10/20/60
  day presets, custom horizons and readable per-day results. Test interactions.
- [x] Run backend/frontend tests, lint, build and live index forecast/backtest.
  Restart only our backend8010, check desktop/mobile and keep services running.

## Semantics

One model step remains one completed daily bar.1/3 days are ultra-short,
5/10 short and20/60 medium presets, not claims about accuracy or strategy.
Index values are points, not CNY prices. Quotes never become model context.
Chinese prices/indices use mainland trading sessions, not calendar days.
The interface retains explicit research and non-commercial model restrictions.

## Verified Results

- Backend83 tests and frontend17 tests passed; Ruff, ESLint, TypeScript,
  production build and Python compilation passed.
- All six indices returned484 daily unadjusted observations through2026-09-04.
- Live SSE Composite forecasts for1/5/10/20/60 sessions succeeded;20 sessions
  ended2026-10-12 and60 ended2026-12-07, correctly skipping published holidays.
- CSI300 ten-session, two-window backtest succeeded. Index snapshots contain
  no order book and no fabricated stock valuations.
- Browser submitted a ten-day SSE Composite forecast successfully. Chinese
  headings, status, errors, chart tools, results and units were inspected.
  The external cloud-share action was omitted from the local chart toolbar.
- Desktop and390px layout checks passed after the hidden browser completed
  its resize event; all three charts contained actual forecast/history paths.
- XSHG calendar4.13.2 is bounded at2026-12-31. Requests beyond the published
  calendar fail before model loading, covered by a service-level test.