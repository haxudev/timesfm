# User, Researcher And Administrator Workspaces

## Current Delivery

The legacy research screen has been decomposed into independent pages sharing
the current restrained navigation, spacing, typography and chart presentation.
Market viewing is now the first navigation entry and the default page.

| Current group | Page | Route | Purpose |
| --- | --- | --- | --- |
| User Viewing | Market quotes | `#market` | Prices, valuations, five-level order book, source/time, stock/index selection |
| User Viewing | Stock prediction | `#prediction` | Existing v2 persisted prediction workflow and model status |
| Research Management | Forecast experiment | `#forecast` | Configurable v1 forecast, one result view at a time, CSV export |
| Research Management | Historical backtest | `#backtest` | Independent inputs, rolling windows and model/baseline comparisons |
| Research Management | Model evaluation | `#models` | Retained candidate metrics, readiness and limitations, read-only |
| Research Management | Data status | `#data` | Source permissions, collection status, dataset coverage, read-only |

Desktop navigation displays both groups. Mobile displays an explicit workspace
selector and only the selected group's destinations. The selector switches page
context, not authenticated identity. Navigation occupies its own layout row and
does not overlay scrolling controls or charts.

Market and stock-prediction views share security selection. Research pages have
their own parameters so an experiment does not silently alter a backtest. Each
visited page retains parameters, results and in-flight work in the current browser
session. Reloading the browser does not persist unsaved form state. Passive quote
and health polling stops on hidden pages; submitted operations are not resubmitted
or cancelled merely by navigation.

Forecast results offer price, return-distribution, history and detail views rather
than three vertically stacked charts. The export action downloads the retained
response. Submission parameters remain visible; editing the form displays a
changed-parameters warning instead of pretending old results match new inputs.
Existing experimental interval caveats remain; no model performance or PIT claim
was upgraded by the UI change.

## Planned Role Boundaries

The following is a future server-side authorization design, NOT implemented access
control. The current preview remains local and unauthenticated, and research pages
are reachable by their hashes. Hiding a navigation group is not a security boundary.

| Action or information | Viewer | Researcher | Administrator |
| --- | --- | --- | --- |
| Read permitted market data and published prediction summaries | Yes | Yes | Yes |
| Request bounded published-model predictions | Product policy and quota | Yes, quota applies | Yes, quota applies |
| Change experimental context, target, device or backtest parameters | No | Yes, bounded job queue | Explicitly granted research permission |
| Inspect unpublished candidate metrics and internal dataset provenance | No | Yes | Yes |
| Start collection/backfill, retry jobs or modify schedules | No | Limited permission if granted | Yes, audited |
| Configure provider credentials and permitted datasets | No | No secret read access | Write/rotate only; no plaintext echo |
| Approve/activate/rollback models | No | Submit candidate for review | Explicit approved workflow, audited |
| Manage users, permissions and retention/backup policy | No | No | Yes, audited |

A user-facing page should receive a deliberately shaped public summary rather
than internal raw responses, file paths, unpublished artifacts or account details.
Research reports may include dataset IDs and validation limitations but not keys.
Administrative settings should show credential configuration status, never the
stored secret. Existing collection/model command-line functions are not exposed
as unprotected Web buttons by this refactor.

## Proposed Navigation Evolution

1. Keep the current user group focused on reading and explicitly requesting a
   prediction. Do not show training controls, provider credentials or model approval
   actions on these pages.
2. Once identity and API authorization exist, show a researcher workbench containing
   experiments, backtests and candidate evaluation to authorized accounts only.
3. Add a separate administrator group for source configuration, collection/task
   operations, model approval, backup and access management only after those
   server-side endpoints and audit records exist. Do not add placeholder controls.
4. Share components and API contracts initially. Separate frontend deployments
   are optional later, but authorization must be enforced identically regardless
   of which frontend or direct API client sends the request.

## Required Security And Release Gates

- Establish authenticated sessions and enforce roles/scopes on every backend
  route, including report reads, job status/cancellation and model operations.
- Restrict job visibility and cancellation to the submitting user or specifically
  authorized operators; apply per-user quotas and bounded experiment resources.
- Audit source configuration, collection/schedule changes, approvals and rollbacks.
  Require explicit confirmation for privileged state changes and preserve provenance.
- Decide the public data/derived-model rights and resolve TimesFM weight usage
  restrictions before any production release. Current candidates remain unapproved.
- Keep secrets server-side, restrict browser origins, add TLS and an appropriate
  session/CSRF policy, then test direct API denial rather than only menu visibility.
- Back up and restore the deployed metadata/model state before enabling admin writes.

These are planned controls. No login, RBAC, schedule editor, model activation UI,
public hosting or new backend endpoint was introduced in this task.

## Code And Compatibility

- App is a small entry point for PredictionWorkspace.
- MarketPage owns the user-facing quote surface; QuotePanel retains its original
  real-time source, refresh, valuation and order-book capabilities.
- ResearchPages supplies independent ForecastExperimentPage and
  HistoricalBacktestPage instances; researchForm handles input conversion/validation;
  ResearchParameters and ResearchResults share display logic, not live form state.
- The internal AdvancedResearch named export was removed, with all repository
  callers/tests migrated. This is an intentional internal component refactor,
  not a change to the v1/v2 backend contracts. External embedders using that
  internal export need to adopt the independent page components.
- Old `#research` bookmarks normalize to `#forecast` with replaceState; they do not
  add duplicate history entries. All six current pages support direct links and
  browser back/forward.

## Verification

- 91 frontend unit tests passed; build/typecheck and ESLint passed.
- 48 Playwright cases passed across 360/390/1280/1440 viewports: 16 live
  market/evidence/navigation checks and 32 fixed-response prediction/research checks.
- Real default market page and separate research results inspected in desktop/mobile
  screenshots. Single chart view, table scrolling, CSV download, independent input
  state, changed-parameter warnings, in-flight completion and history navigation tested.
- No new real forecasting/backtest jobs were issued for result UI checks; those use
  fixed responses. No backend, market source, model, credential or container changes.
- Existing Plotly bundle remains about 4.40 MB minified; optimization is a separate
  task. The local preview still requires its existing frontend/report servers and WSL.

Preview: `http://127.0.0.1:5175/`. Use the sidebar on desktop or workspace selector
and bottom navigation on mobile. No git commit or push was performed.