# Free Data Sources: 2026-09-06

## Scope And Result

The requested free-source set is Tushare, AKShare and BaoStock. Tushare credentials
were persisted in the repository-root Git-ignored `.env`; the value is not included
here. No subscription/points were purchased, no alternate account or endpoint was
used to bypass restrictions, and no active model or automatic provider was switched.

| Source/capability | Actual observation | Limit |
| --- | --- | --- |
| Tushare daily | Initial sample returned 6 rows | Later qfq collection was rate-limited; initial access is not unlimited availability |
| Tushare adj_factor | Initial sample returned 6 rows | No historical revision/PIT guarantee |
| Tushare index_daily | Initial sample returned 6 rows | Small sample, no whole-market coverage conclusion |
| Tushare trade_cal | Initial range returned 8 calendar rows | Cross-check only, existing exchange calendar was not replaced |
| Tushare listed stock_basic | One listed-stock record returned | Later universe query was rate-limited |
| Tushare delisted stock_basic | Rate-limited | Not evidence that delisted records are absent or permission-denied |
| Tushare industry Y/N | Both permission-denied | No purchase and no fabricated industry fallback |
| AKShare | Existing September 6 probe: 2 stocks +2 indices succeeded, industry failed | Retained as default; not rerun merely to increase request count |
| BaoStock | 2 stocks +2 indices completed, all requested price fields present; industry response downloaded | 5,215 SH/SZ stock records selected, not full A-share/BJ coverage |
| BaoStock dated industry | 002594.SZ query for 2020-01-06 returned one record | Classification/update date is not historical publication evidence |

All responses remain untrusted for strict historical PIT acceptance. BaoStock's
reported classification is not silently substituted for SW classification. No
financial-reporting/PIT financial-statement pipeline was implemented in this change.

## Local Artifacts

| Artifact | Snapshot ID |
| --- | --- |
| Tushare eight-check capability report | `b5f4556c6cbb6a6b74aa30b546cacf92c0b6990184d4f503efd2e2263f2282aa` |
| Tushare failed universe collection | `7992bce030aaff07300a07399734602e209b2ad1a8e1c546b49239b852200289` |
| Tushare normalized saved-sample qfq | `3784959d65c308816a65e3b0ab712767a84a4c6e7f0c90f97b4ad6344870538b` |
| BaoStock successful collection | `5ad508749d0e2a2fc52f6fa8c50cf5f61ec8d129dc5c91d60fddd96fa3bc4f70` |
| BaoStock short raw bars | `a633b10e14987a07061b5505efb7c012671ed1034c7432de5d1891b835bf0ca2` |
| BaoStock short qfq bars | `279b4b1ac074f662c3dec010b29bb401edf56b319876b509cd8c2c4c4cc0fac6` |
| BaoStock dated-industry sample | `1eb00c20d60b8872c342946969e141e83a0527fd721fb58453e0c8393babde39` |

Stores: `.local/tushare-check-20260906`, `.local/tushare-data-20260906` and
`.local/baostock-data-20260906`. The normalized Tushare sample was produced OFFLINE
from the already captured daily/factor responses after subsequent calls were limited;
it was not a successful second network qfq collection. Its metadata references both
original snapshots. Range: 2026-08-28 through 2026-09-04, six observations.

BaoStock sample stocks were 600000.SS and 600004.SS; indices were 000001.SS and
399001.SZ, requested 2026-08-03 through 2026-09-04. They are not the same stock
sample as the original Tushare access probe, so no cross-provider price agreement
or accuracy claim is made. The original five-year AKShare experiment is unchanged.

## Integration

- Backend Settings reads case-insensitive TUSHARE_API_KEY from `.env` or environment;
  also supports STOCK_FORECASTER_TUSHARE_API_KEY. Secret value excluded from repr/dumps.
- ResearchProvider accepts `provider='akshare'|'tushare'|'baostock'`; credentials are
  not carried in subprocess command arguments. Maximum fetch budget remains45s,
  stdout16MiB. Tushare query uses fixed HTTPS with12s per-call deadline and rejects
  redirects, unknown fields, malformed shape and possible row-limit truncation.
- Explicit `probe`/`bootstrap --provider` plus `source-fetch` and `tushare-access`
  CLI commands. Daily scheduler remains AKShare; no surprise automatic failover.
- Tushare OHLC qfq requires a unique positive factor for every bar. Volume/amount
  remain activity values, not adjusted quantities. Units are normalized separately.
- BaoStock stock bars require matching adjustflag. Index raw responses use their
  supported field contract. Suspension fill rows are retained only in original data;
  normalized histories have gaps rather than manufactured trading prices.
- Source request/response snapshots preserve provider and operation attribution.
  A source date or collection timestamp never upgrades `trusted` or `point_in_time`.
- Web sources are grouped by provider. Latest collection failure remains visible
  even if an earlier capability probe succeeded. Unrequested/error text is not sent
  to the browser. Existing experiment report matching remains exact by dataset ID.

## Preview

`http://127.0.0.1:5175/#data` shows the three free sources. Only the local report
server8013 was restarted, reading `.local/web-preview-free-sources-20260906` with
seven report snapshots. No `.env`, raw market frames, model artifacts or task history
were copied into that preview store. Existing8012 prediction containers are unchanged.
Changing credentials later requires explicitly rerunning permission checks and
reseeding a new preview; this report-only Web view does not issue vendor requests.

## Verification And Next Gate

- 438 backend tests passed, with 24 existing Pandas categorical deprecation warnings.
- 82 frontend tests, 12 actual Web navigation/source-report tests across 360/390/1280/1440,
  build, Ruff and ESLint passed. Screenshots inspected, no new overlap/overflow.
- A literal secret-presence scan over301 candidate source/build/research files found
  no supplied credential outside `.env`. It is a focused check, not comprehensive DLP.
- No new trained models, PIT approval, automatic source switch or production release.
- The small new samples do not add enough validated history for another trained
  model. Respect quota-reset rules, persist permitted history in bounded runs, then
  repeat dataset readiness checks. Industry/publication/stock-universe evidence and
  predictive effectiveness remain separate gates; do not buy access automatically.

## Official Contracts Consulted

- [Tushare HTTP contract](https://tushare.pro/document/1?doc_id=130)
- [Tushare daily](https://tushare.pro/document/2?doc_id=27)
- [Tushare adjustment factors](https://tushare.pro/document/2?doc_id=28)
- [Tushare securities](https://tushare.pro/document/2?doc_id=25)
- [Tushare SW membership](https://tushare.pro/document/2?doc_id=335)
- [BaoStock daily bars](https://www.baostock.com/mainContent?file=stockKData.md)
- [BaoStock dated industry](https://www.baostock.com/mainContent?file=stockIndustry.md)