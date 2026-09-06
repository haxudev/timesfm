# Research Web Release Decision: 2026-09-06

## Decision

Accept a loopback-only, non-production research preview. Do not approve candidate
LightGBM signals for production. Current sources support bounded price-history
experiments and existing exploratory TimesFM/GARCH use, not a validated full-market
investment service. This release adds visibility into evidence, not predictive power.

The user was unavailable to select a deployment target. The conservative choice
was independent local ports and no modification of existing containers, rather
than public exposure, paid procurement or model promotion.

## Current Support

| Capability | Current evidence | Decision |
| --- | --- | --- |
| Free price-history collection | 2026-09-06 probe: 2 stocks + 2 indices succeeded, raw/qfq fields complete | Suitable for bounded research; not an SLA or full-market acceptance |
| Historical industry source | Bounded AKShare industry retries failed again | Not available in this probe; publication timing also remains unverified |
| Price/index LightGBM experiment | 30 current SZ stocks, 1,214 sessions, 35,589 feature rows; all six heads trained | Enough for a baseline experiment, not representative historical-universe validation |
| Candidate prediction quality | No horizon beats zero-return MAE; 5/20-day Brier worse than training-prior baseline | No promotion |
| TimesFM | 29/30 historical contexts pass length checks; existing served example available | Pretrained exploration only, no new fine-tuning or broad efficacy claim |
| GARCH | 27/30 histories pass length checks; one candidate fit verified previously | Risk-only research; other eligible symbols not fitted in this change |
| Web evidence | Actual source/readiness/training reports joined by exact dataset IDs | Available read-only in local preview |
| Public/commercial production | Model/data rights, validation and operational controls incomplete | Blocked |

September 6 is not a trading day. The probe's latest completed session is
2026-09-04; this is not evidence of stale collection. It also does not establish
that every individual stock has a current usable quote.

## Accessible Preview

- Frontend: `http://127.0.0.1:5175/`, select data status or model evaluation in the navigation.
- Report API: `http://127.0.0.1:8013/api/v2/evidence`.
- Existing frontend/backend: 5174/8012 remain unchanged and running.
- Preview report store: `.local/web-preview-20260906-verified`, exactly four report
  snapshots, no raw provider snapshots, task history or model directories.
- Predictions are forwarded through Vite to existing 8012. Read-only evaluation
  requests are forwarded to 8013. The report service does not run inference.
- This is a development preview, not durable hosting. Keep the preview terminals
  and the existing WSL containers running. No startup service or public listener
  was installed. `research-preview` proxy settings do not apply to a static build.

The page shows distinct probe timestamps and training dataset dates, incomplete
evidence states, missing numbers as `--`, three horizon comparisons, input-length
counts, and immutable report references. It offers no train/activate/promote button.
Candidates are explicitly unapproved; a complete API response means reports are
available, not that the data or models satisfy production requirements.

## Evidence References

| Object | Snapshot ID |
| --- | --- |
| September 6 source check | `ba494dab23da47dd1ec8ade85909fb0c2db8cea746c80261e69e3bd4ad1c0c46` |
| September 5 collection | `5e9d9356289420bbfefd650a6c52479d779bbc694f6b8f202b26a56bc7ad7bf5` |
| Matching readiness | `df776e0d3b3dbb12bdbdb3eab661a7c09dd2863502f1338d013b762123177714` |
| Candidate training | `1df82bf2455abb50b3f00ec2fed14c7ef5badf3de514a31325b605e30d0e8f97` |

See [the training measurements](2026-09-05-data-readiness.md) for exact sample
selection, splits and metrics. The September 6 probe is an independent sample;
it does not upgrade the September 5 dataset's trust or historical publication status.

## Staged Release Plan

### 1. Local Evidence Preview: Accepted

- Keep candidate metrics separate from current prediction cards.
- Serve only allowlisted offline summaries, preserve exact report relationships,
  and do not expose raw datasets or model files through the preview API.
- Retain nullable/error/partial states and real desktop/mobile browser checks.
- Rollback: stop only the new 5175/8013 preview processes. No existing service,
  persistent Docker volume or active artifact needs to be changed.

### 2. Stronger Data And Forward Evaluation: Required Next

1. Add a separately attributed free-source verification path, such as the previously
   researched BaoStock interfaces, for historical listings/delistings, suspension/ST
   and corporate actions. Verify actual field coverage and permitted retention;
   do not assume a dated response proves historical publication availability.
2. Freeze the universe-selection rule before observing validation outcomes. Cover
   different exchanges and listing vintages, and account for delisted names. Keep
   any current-universe experiment explicitly subject to survivorship bias.
3. Preserve unadjusted bars and dated action/factor versions separately. Reconcile
   sources without merging incompatible price bases; disclose whether sources
   share an upstream provider. Missing events remain blockers, not inferred values.
4. Specify an Asia/Shanghai `decision_as_of`, publication/first-observed semantics
   and next-session execution policy. Fix the existing strict close/midnight cutoff
   mismatch with tests before claiming strict PIT readiness.
5. Run predeclared walk-forward folds, independent calibration and label-end purge.
   The already inspected holdout must not become a repeatedly tuned final test.
   Compare zero-return and training-prior baselines on identical samples; use
   date-aware/block uncertainty estimates for correlated/overlapping observations.
6. Evaluate trading costs, execution timing, suspension/limit rules and coverage
   failures. Collect prospective paper predictions without reconstructing past
   forecasts as if they had been issued then. Do not start live trading.

These steps are planned, not implemented by the Web preview. No additional data
provider, financial statements feed, historical action ledger or multi-fold trainer
was added in this change. No new models were trained on September 6.

### 3. Shared Or Production Hosting: Conditional

- Resolve model and data rights first. The currently pinned TimesFM 3.0 weights
  are non-commercial/non-production; choose properly licensed weights or obtain
  permission before production hosting. SDK source licensing is not data licensing.
- Decide private research versus public/commercial scope and review API access,
  retention, redistribution and derived-model rights for each provider. No purchase
  is authorized by this plan.
- Add authenticated access, authorization for job/report operations, request quotas,
  TLS, secret handling and a production reverse proxy. Never expose the Vite dev
  server or current unauthenticated job endpoints as a public service.
- Use a deliberate versioned report export/import path with preserved provenance
  when combining experiment and deployed stores; do not copy over an active database.
- Measure bounded multi-user/API/worker behavior and full selected-universe load.
  Validate backup/restore, rollback, observation freshness and failure alerts on the
  final deployment topology before making availability claims.
- Require human approval for any model activation or public release. No current
  LightGBM candidate meets an effectiveness promotion decision.

## Verification

Navigation update on the same date: four independent destinations replace stacked
top-level disclosure panels; desktop sidebar and mobile bottom navigation preserve
state and expose experiment tabs. Final navigation verification: 81 frontend unit
tests, 36 Playwright tests (12 live navigation/evidence cases and 24 prediction
fixture cases), lint and build passed. Backend behavior was not changed in this
navigation update. See [the navigation plan](../superpowers/plans/2026-09-06-workspace-navigation.md).

- Backend: 412 tests passed; 24 pre-existing Pandas categorical deprecation warnings.
- Frontend: 72 unit tests passed; build and lint passed.
- Browser: 32 Playwright tests passed across 360/390/1280/1440 viewports. Eight use
  the real seeded report API; the other 24 retain existing fixture prediction tests.
- Actual default prediction data from existing 8012 is visible alongside real
  evidence; no new forecast was submitted during the evidence browser tests.
- Desktop/mobile screenshots inspected; no document/panel horizontal overflow.
- Cross-origin failure discovered in screenshots was reproduced with a new actual
  prediction assertion and fixed using the preview-only same-origin proxy.
- Backend summaries sanitize unknown error/limitation text. Seed rejects recognized
  sensitive report fields before creating the destination; this is not comprehensive
  DLP. Only reviewed local report files should be imported or shared.
- Existing Plotly bundle-size warning remains (about 4.39 MB minified). It is an
  optimization item before broader hosting, not a hidden acceptance success.
- No existing container restart, paid account access, new dependency, model activation,
  schedule enablement, git commit or push was performed.