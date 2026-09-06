# Audience-Oriented Workspace Refactor

**Goal:** Extract quote viewing, forecast experiments and historical backtesting into independent pages using the current restrained UI, separating viewing workflows from research/administration information.

**Decision:** User requested market viewing as the first sidebar entry and separate forecast/backtest pages. User delegated routine decisions while unavailable. Implement a shared shell with two explicit navigation groups, not separate deployments or simulated security roles.

## Information Architecture

| Audience | Page | Route | Primary behavior |
| --- | --- | --- | --- |
| User viewing | Market quotes (first/default) | #market | Stock/index selection, prices, order book, explicit refresh |
| User viewing | Stock prediction | #prediction | Existing v2 predictions, queued tasks and signals |
| Research management | Forecast experiment | #forecast | Configurable v1 experiment, results/chart/table/export |
| Research management | Historical backtest | #backtest | Own parameters, rolling windows, baseline comparison |
| Research management | Model evaluation | #models | Read-only candidate metrics and limitations |
| Research management | Data status | #data | Read-only source/coverage/access evidence |

- Desktop shows both labeled navigation groups; market quotes is first.
- Mobile uses a workspace selector and only that group's navigation entries, never six cramped bottom buttons. Bottom navigation owns layout space, outside the content scroller.
- Preserve hash direct links/back-forward, map old #research to #forecast, and retain each visited page's state. Read-only first screen must not initialize experiments or submit work.
- Market and user prediction share the selected instrument; research pages own separate form and result state. Requests/results must identify their frozen input rather than the current edited form.
- Extract reusable experiment form validation and result rendering instead of preserving a monolithic legacy page. Use compact parameter/results layout and one chart view at a time; keep CSV export, index selection and all existing API options.
- Pause hidden quote/health polling while retaining submitted operations. Do not introduce automatic collection/training/model activation.

## Authorization Boundary

This is information architecture, not authorization. Existing local preview remains unauthenticated. A future public user surface should receive read-only/public prediction DTOs, researchers should run experiments, and administrators should manage credentials/collection/activation with server-side checks and audit logs. Do not add fake admin buttons or expose credentials in this refactor.

## Verification

- [x] Default market-only reads, first menu order, six direct routes and old route alias.
- [x] Separate page/form/results state, retained queued jobs, explicit commands only.
- [x] Legacy forecast/backtest validation, index/CSV/results behavior preserved through new components.
- [x] Desktop/mobile group navigation, back-forward, scrolling, charts and no overlap.
- [x] Full frontend unit/build/lint and browser regression; keep backend and sources unchanged.

## Delivery

- Removed the old `AdvancedResearch` implementation and its internal named export;
	repository callers/tests now use MarketPage, ForecastExperimentPage and
	HistoricalBacktestPage. App only installs the current workspace shell.
- Shared research parameter rendering uses unique radio names. Pure form conversion
	and validation live outside React components. Separate page instances retain
	independent form/mutation state. Results display submission parameters and changed-input warnings.
- Retained previous stock selection across external search/index changes, and
	normalized the old research hash using replaceState. Both defects reproduced
	in failing tests before their fixes.
- Final verification: 91 frontend unit tests, 48 Playwright cases across
	360/390/1280/1440, build and ESLint passed. Browser coverage includes 16 live
	market/evidence/navigation checks and 32 fixture prediction/research-result checks.
- Inspected desktop/mobile quote, experiment and backtest screenshots. No horizontal
	page overflow; mobile navigation occupies its own row; one chart rendered at a time;
	CSV download and result/parameter retention passed.
- No backend/model/data-source/credential changes or active model promotion. No
	new dependencies, public deployment, git commit or push. Same local preview5175.
- Role architecture and future authorization plan documented in
	`docs/research/2026-09-06-audience-design.md`; role enforcement is not implemented.