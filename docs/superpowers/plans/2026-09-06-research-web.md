# Research Web Preview Plan

**Goal:** Expose actual data-source checks and dataset-linked candidate evaluation in the existing prediction-first Web experience, without model promotion.

**Decision:** The user requested continuing source/data assessment, launch planning and Web integration. Deployment target clarification was unavailable; proceed with local research-only preview on independent loopback ports. Preserve existing 5174/8012 services, no public exposure or procurement. TimesFM's current non-commercial/non-production weights are not cleared for a production service.

## Design

- Add an offline, bounded `/api/v2/evidence` endpoint returning allowlisted summary fields from immutable collection, readiness and training report snapshots.
- Match readiness to the chosen training report's exact bars/index dataset IDs. Do not mix latest unrelated reports. Return separate source-check and training-dataset provenance.
- Summaries expose research-only status, history coverage, exclusions, model context eligibility, horizon sample counts and model/baseline metrics. No raw responses, model binaries, local paths or arbitrary exception messages.
- Keep default screen single-stock predictions. Add a compact expandable research evidence section with loading, empty, partial, error and retry states. Do not insert experimental values into prediction cards.
- The `research-preview` Vite mode uses same-origin `/api` requests: `/api/v2/evidence` proxies to loopback 8013, all other `/api` requests to existing loopback 8012. This avoids changing the existing backend CORS configuration. A separate evidence origin is also supported for environments with configured CORS. Unified deployment remains a later migration step.
- Use a separate preview store containing hash-verified report snapshots only, not the original experiment database or active model directories. Report serving does not collect or fit anything.
- A read-only preview backend refuses mutation/prediction requests. No scheduler or worker is enabled in the evidence server.

## Verification And Steps

- [x] API tests: empty store, offline access, matching dataset IDs, mismatched report exclusion, bounded/sanitized output, immutable source reports, read-only mode.
- [x] Implement report summary API and validate focused backend tests.
- [x] Frontend tests: actual values, non-PIT/research-only status, independent source checks, baseline comparison, loading/error/retry/empty states, unchanged prediction workflow.
- [x] Add local report-only preview seed/run command and exercise it on actual 2026-09-05 experiment and 2026-09-06 source probe.
- [x] Build/lint/tests and Playwright desktop/mobile actual-API checks; inspect screenshots and overflow.
- [x] Write launch decision and checklist, with URLs, data/source limitations and explicitly blocked public/production promotion.

## Gates

Local preview acceptance is usability and evidence fidelity, not prediction performance. Public/production release is blocked pending model/data licensing, historical universe/corporate-action provenance, a validated decision-time policy, representative walk-forward evidence, authentication/rate limits/TLS, backups and restore checks, and operational capacity testing. Current three LightGBM horizons did not beat zero-return MAE; candidates remain unapproved.

## Verification Record

- Source probe 2026-09-06: two stocks and two indices succeeded, industry request exhausted transient retries. Report `ba494dab23da47dd1ec8ade85909fb0c2db8cea746c80261e69e3bd4ad1c0c46`.
- Four original reports imported into `.local/web-preview-20260906-verified`, preserving IDs and original collection times; no models, raw provider snapshots or jobs imported.
- Review fixes: absent collection reports produce partial evidence; known sensitive report fields are rejected before a preview destination is created.
- Browser screenshots exposed a cross-origin prediction failure missed by the original panel-only assertions. Added a real prediction-origin assertion, reproduced failure, enabled the same-origin proxy and revalidated.
- Backend: 412 tests passed with 24 existing Pandas categorical warnings. Frontend: 72 unit tests passed. Playwright: 32 tests passed across 360/390/1280/1440 viewports, including eight actual evidence-API checks and existing fixture prediction flows.
- Final preview: `http://127.0.0.1:5175/`; report API: `http://127.0.0.1:8013/api/v2/evidence`. Existing 5174/8012 containers preserved. This is a local development preview, not production hosting.