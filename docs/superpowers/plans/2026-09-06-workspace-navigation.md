# Task-Oriented Workspace Navigation

**Goal:** Replace vertically stacked top-level disclosure sections with separate, persistent task views.

**Approved direction:** The user requested a friendlier navigation/sidebar arrangement and delegated routine decisions while unavailable. Use a compact desktop sidebar and four-item mobile bottom navigation. Keep the prediction-first visual language and all data/model limitations.

## Design

- Primary destinations: prediction, data status, model evaluation, research experiments.
- Use hash links with active navigation semantics and browser back/forward support; no new router dependency.
- Only the selected view is visible. Mount each on first visit, preserve its state afterwards. Changing destinations must not abort or resubmit a prediction job.
- Shared report queries remain cached. Pause hidden-view quote/health reads, but let existing submitted jobs complete safely.
- Split evidence presentation into data/source coverage and model/baseline evaluation; preserve exact report identity and nullable data semantics.
- The experiments view has local tabs for quotes, forecast experiments and backtests, retaining shared settings and results. Keep the standalone legacy component behavior compatible with existing tests.
- Secondary provenance/limitation details may remain collapsed; primary navigation must never scroll to stacked modules.
- Desktop sidebar is visible without overlaying charts. On narrow displays use a viewport-height grid with scrolling main content and a separate bottom navigation row, icon and text labels, sufficient touch targets and safe-area padding.
- Do not change backend behavior, data collection, model activation, source data, container services or licensing gates.

## Checks

- [x] Add navigation tests for exclusive views, lazy queries, retained stock/horizon/task state, hash navigation and experiment tabs.
- [x] Implement primary view shell and responsive navigation; run focused tests immediately.
- [x] Split evidence and experiment surfaces while preserving state and query isolation; run focused tests.
- [x] Adapt E2E to navigation, then verify actual API plus fixture workflows at 360/390/1280/1440 and inspect screenshots.
- [x] Run full frontend tests, lint, typecheck/build; document preview and update status.

## Results

- Primary routes: `#prediction`, `#data`, `#models`, `#research`; default prediction.
- Research tabs: quotes, forecast experiments and historical backtest. Shared inputs
	stay intact while switching tabs or primary views. Standalone legacy research
	rendering remains compatible.
- Existing pending jobs survive navigation without duplicate submission/cancellation;
	hidden-view quote and health polling pauses. Evidence uses one shared cached query.
- Read-only review's potential evidence radio conflict was ruled out: data view does
	not render a horizon selector; only model evaluation does. Both unit and actual
	browser tests verify the split and retained model horizon.
- A skip-link hash conflict was caught and fixed with a failing unit test. Fixed
	overlay navigation was replaced with a separate grid row after screenshot review;
	a geometry test first reproduced overlap, then verified non-overlapping bounds.
- Final 81 frontend unit tests and 36 browser tests passed. Viewports: 360/390/1280/1440.
	Desktop and mobile screenshots inspected, no horizontal overflow, keyboard tab
	switching and browser back/forward passed; returned prediction chart remained nonblank.
- Same local preview at `http://127.0.0.1:5175/`; backend/data/models/containers untouched.
	No new dependencies, public deployment, model activation or git commit/push.