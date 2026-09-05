# Prediction Workspace Test Delta

Baseline (2026-09-05): 3 Vitest files, 23 tests passed before this change.

Scope: frontend only, preserve dirty changes, no commits and no user service lifecycle changes.

## Execution

- [x] Read the existing UI, tests and backend v2 contracts.
- [x] RED: first 5 HTTP-boundary component tests failed against the original App. First GREEN: 5 new tests plus all 16 advanced v1 UI tests passed.
- [x] GREEN: typed v2 requests with abort support; v1 requests and CSV retained.
- [x] Existing App preserved as the exported AdvancedResearch component, mounted only when expanded. Its original regression tests target that component.
- [x] Second RED: 4 missing chart/staleness behaviors failed, 8 tests passed. Second GREEN: all 12 v2 tests passed.
- [x] Chart inputs verified: origin zero, last 60 normalized prices, true TimesFM path, LightGBM target only, no quantile band; missing origin never falls back to a live quote.
- [x] Playwright request-boundary tests and screenshots at 360x800, 390x844, 1280x800, 1440x900 ran using installed Windows Chrome.
- [x] Final gates: 35 Vitest tests passed (baseline 23, delta +12); 16 Playwright tests passed; production build and lint passed.

All numerical browser/test data belongs to explicitly named fixtures, never application defaults. Model readiness and validation claims must come from the API.

## Commands

Run from the repository root:

```sh
npm --prefix apps/stock_forecaster/frontend run --silent test -- --run
npm --prefix apps/stock_forecaster/frontend run --silent build
npm --prefix apps/stock_forecaster/frontend run --silent lint
npm --prefix apps/stock_forecaster/frontend run --silent test:e2e
```

Playwright connects to an already-running frontend at http://localhost:5173. Set PLAYWRIGHT_BASE_URL to test a different existing frontend. The config deliberately has no webServer entry: it never starts or stops the user's server. All /api/ requests are intercepted; no model training, live quote fetching or backend submissions occur during E2E tests.

Windows uses the installed Chrome channel by default. PLAYWRIGHT_CHANNEL can select another installed channel (for example msedge). On other systems the default is Playwright Chromium; install it in that test environment with `npx playwright install chromium` from this frontend directory when needed.

Successful screenshots are in test-results/*/prediction.png and test-results/*/partial.png (ignored by git). The four prediction screenshots were also visually inspected. Tests check rendered SVG path lengths, one Plotly chart, viewport overflow and metric/button bounds; they do not substitute a mocked Plotly component for browser rendering. Vitest mocks only the Plotly renderer, keeping the application, chart-input construction and HTTP client real.

## Remaining Boundaries

- No real-model E2E run was performed in this frontend-only task. Fixture completion does not demonstrate model availability, calibration quality or investment performance.
- The current v2 contract has no validation-results endpoint. The expandable validation area explicitly reports unavailable sample-out-of-sample evidence and shows bundle/snapshot/artifact provenance only.
- A newer quote date triggers a possible-staleness warning. Quote as_of is never treated as prediction origin or issued_at; no unsupported exchange-calendar freshness claim is inferred.
- Vite reports the Plotly-containing bundle is larger than 500 kB (about 4.38 MB minified / 1.34 MB gzip). Build succeeds; no new UI framework was introduced.
- No backend files were edited, no user server was started or killed, and no commits or branches were created.