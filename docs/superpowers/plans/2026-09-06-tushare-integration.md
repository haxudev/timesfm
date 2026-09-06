# Tushare Data Integration

**Goal:** Persist the user-provided key locally, verify actual API access and connect the user-selected free sources Tushare, AKShare and BaoStock without changing trust or active models.

## Boundaries

- Root `.env` is Git-ignored and excluded from Docker build contexts. Credentials are backend-only, secret-typed, excluded from serialized settings, never passed in subprocess arguments or report metadata.
- Use the existing httpx dependency with the official HTTPS API endpoint; do not downgrade TLS or follow redirects with credentials.
- Bounded calls, response size/row guards and safe error codes. Provider errors must not contain returned messages or request bodies.
- Keep AKShare the default; Tushare is explicitly selected. No mixed-source price splicing, implicit subscription purchase, trust upgrade, model promotion or automatic whole-market run.
- Current API availability, empty data, permission denial and transient failure must remain distinct. Historical industry effective dates are not publication timestamps.

## Steps

- [x] Add Git-ignore protection and persist the supplied key in `.env`; verify presence/ignored/untracked using boolean output only.
- [x] Test and implement secret settings and the bounded HTTPS client; verify red/green.
- [x] Probe actual account permissions for prices, factors, index, calendar, listings and historical industry using small samples.
- [x] Add explicit provider/CLI integration and data normalization for supported capabilities, with immutable raw responses and honest limits.
- [x] Run focused/full checks and a bounded real collection; expose a sanitized capability report in the existing data-status view if feasible.
- [x] Document actual availability, blockers and operating commands; preserve old sources, datasets and active services.

## Outcome

- Tushare initial daily/factor/index/calendar/listed probes returned data; delisted request and subsequent collection were rate-limited; industry current/history were permission-denied. No quota purchase or retry loop. Six retained daily/factor rows normalized offline with exact source references.
- BaoStock 0.9.3 installed without upgrading other dependencies. Two-stock/two-index short probe succeeded, and a dated 2020 industry sample returned. Unverified Beijing coverage remains explicitly unsupported.
- CLI `probe|bootstrap --provider` and `source-fetch` now support explicit providers; automatic daily/scheduler behavior remains AKShare. No mixed-source history or model activation.
- Data Web view shows independent latest collections and capability checks. Seven report snapshots imported to `.local/web-preview-free-sources-20260906`; only local report server8013 restarted. Frontend5175 and existing8012 containers preserved.
- Final438 backend tests,82 frontend tests,12 focused real browser cases, Ruff/ESLint/build passed. Existing24Pandaswarnings and Plotlylarge bundle remain.
- Secret-safe scan of301 tracked/untracked candidate/build/research files found no literal supplied credential outside `.env`. Git-ignore/untracked status verified; no secret recorded in docs, memory, frontend or command arguments.
- Detailed measured results and limitations: `docs/research/2026-09-06-free-data-sources.md`.