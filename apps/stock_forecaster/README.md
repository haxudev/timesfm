# TimesFM Stock Forecaster

## Prediction Research Workspace

The default screen is market viewing, first in the **User Viewing** navigation
group. The adjacent stock-prediction page supports 1/5/20 exchange sessions and
persisted forecasts, displaying available returns, calibrated upside probability
and independent cumulative volatility. **Research Management** contains independent
forecast-experiment, historical-backtest, model-evaluation and data-status pages.
The old monolithic Advanced Research UI has been replaced by dedicated pages;
the underlying v1 forecast/backtest APIs remain unchanged.

- TimesFM provides a cumulative return path, not a calibrated joint price band.
- The official checkpoint is pinned to revision
  `43046b85ec22d584a13f8098c2ed39c889e129c2`; the resolved identity is stored with
  predictions. Custom checkpoints do not inherit this official repository's SHA.
- LightGBM has one return regressor and calibrated classifier per horizon. It
  requires verified historical price/index/industry inputs; absent artifacts or
  inputs are explicitly unavailable, never substituted with a 50% probability.
- GARCH(1,1), Student-t, zero mean provides cumulative volatility. Fitting is an
  explicit background operation; prediction does not optimize model parameters.
- Parquet/DuckDB preserve immutable input and evaluation snapshots; SQLite stores
  durable jobs, cancellation requests and result references. Native model files
  have integrity manifests and are included in backups. No pickle is loaded.

### WSL2 / Docker

Run inside WSL from this directory:

```sh
docker compose up -d --build --wait
```

Default host addresses are `http://localhost:5173` and `http://localhost:8010`.
If those ports already serve another app, use overrides consistently:

```sh
export FORECASTER_API_PORT=8012 FORECASTER_WEB_PORT=5174
docker compose -p timesfm-research up -d --build --wait
```

This implementation's acceptance instance uses **5174 / 8012** to preserve the
existing local servers. Data and model artifacts live in a Docker named volume;
Hugging Face weights have a separate volume. Backend/worker run as UID 10001.
The init service creates an internal random token without printing it; it is not
committed, exposed to the browser, or included in data backups. Do not use
`docker compose down -v` unless you intend to delete these volumes.

The default worker processes interactive jobs only. Enable automatic collection
and forecasting explicitly after checking the source report and resource budget:

```sh
docker compose -f docker-compose.yml -f docker-compose.scheduled.yml up -d
```

This enables one worker with an APScheduler coordinator at 18:00 Asia/Shanghai.
Startup recovery is bounded and does not fabricate past prospective predictions.
The WSL host must be running; `restart: unless-stopped` cannot run a powered-off PC.
Weekly training requires an explicitly configured, reviewed dataset. It produces
research candidates, not automatically validated replacements.

### Free Data Sources

Supported research sources are **AKShare**, **Tushare**, and **BaoStock**. AKShare
remains the default; source selection is explicit and no provider failure silently
switches the price basis. No data subscription or quota purchase is performed.

For Tushare, place `tushare_api_key=YOUR_KEY` in the repository-root `.env`, or set
`TUSHARE_API_KEY` / `STOCK_FORECASTER_TUSHARE_API_KEY` in the backend environment.
Run the commands below from the repository root so the relative `.env` is loaded.
The key is a backend `SecretStr`, excluded from settings serialization/repr, Git
and Docker build contexts. Do not use a `VITE_` variable or put a key in CLI arguments.
Docker containers need an explicit runtime environment configuration and updated
image to use new adapters; the running prediction containers were not changed.

```sh
python -m stock_forecaster.research.cli --root .local/tushare-check tushare-access
python -m stock_forecaster.research.cli --root .local/tushare-data probe --provider tushare --limit 2 --index-limit 2 --rate-seconds 2
python -m stock_forecaster.research.cli --root .local/baostock-data probe --provider baostock --limit 2 --index-limit 2
python -m stock_forecaster.research.cli --root .local/tushare-data source-fetch bars --provider tushare --ticker 600519.SS --start 2026-08-03 --end 2026-09-04 --adjustment qfq
python -m stock_forecaster.research.cli --root .local/baostock-data source-fetch industry --provider baostock --ticker 002594.SZ --end 2020-01-06
```

`probe`/`bootstrap` use a provider's stock universe; a denied or rate-limited universe
request stops that batch and persists the failure. `source-fetch` can separately
request `bars`, `factors`, `calendar`, `securities`, `industry` or `universe` where
the selected provider implements it; `--status D` selects Tushare delisted securities.
Inspect actual permissions first. Do not repeatedly run examples after a quota error.
`tushare-access` returns a report, so command completion is not proof all checks passed.
Quota/auth/permission errors are distinct and not automatically retried.

Tushare uses official HTTPS POST with fixed destination, no redirects and bounded
responses. Daily volume is converted from hands to shares, amount from thousands
of CNY to CNY. Local qfq uses each day's positive factor divided by the last requested
bar's factor; missing/duplicate/mismatched factors are refused. Original daily and
factor rows are retained with record types. This is a current-response vintage,
not a reconstructed historical PIT price archive. Truncation at a documented row
limit is rejected; this adapter does not claim unlimited automatic pagination.

BaoStock uses its official SDK in the same bounded subprocess mechanism. Its
return-based qfq convention is explicitly recorded and must not be spliced with
Tushare/AKShare qfq. Suspension fill prices are excluded from normalized bars while
original rows and ST/status fields remain in raw snapshots. Current adapter coverage
is verified SH/SZ only; Beijing routes fail explicitly. Dated industry queries retain
the vendor's classification and update date, not fabricated effective/publication times.

As of 2026-09-06, Tushare sample prices/factors/index/calendar/listed data returned,
but later calls were rate-limited and SW industry calls lacked permission. BaoStock
completed the short stock/index probe. See [the measured source report](../../docs/research/2026-09-06-free-data-sources.md).
Web **data status** shows per-source collections and earlier capability checks
separately. Existing trained candidates remain unapproved and use their original data.

### Existing Research Commands

Use the same Compose project/port options as at startup. These operations are
explicit and may access data sources; normal unit tests do not.

```sh
docker compose exec research-worker stock-forecaster-research probe --limit 2 --index-limit 2
docker compose exec research-worker stock-forecaster-research bootstrap --limit 100
docker compose exec research-worker stock-forecaster-research fit-garch 600519.SS
docker compose exec research-worker stock-forecaster-research daily --limit 100 --enqueue-only
docker compose exec research-worker stock-forecaster-research score
docker compose exec research-worker stock-forecaster-research replay JOB_ID
```

Omit `--limit` only for an intentional full-market collection. A limited run is
always marked partial and cannot establish full-universe capacity. Current-vintage
qfq history is refreshed as a consistent window rather than appending differently
adjusted prices. This is intentionally conservative and is not yet an efficient
corporate-action-aware incremental warehouse.

Train from reviewed snapshot IDs, never by changing free-source trust flags:

```sh
docker compose exec research-worker stock-forecaster-research train \
  --bars BARS_SNAPSHOT --index INDEX_SNAPSHOT --industries INDUSTRY_SNAPSHOT \
  --train-end 2023-12-29 --validation-end 2024-03-29 \
  --calibration-end 2024-06-28 --test-end 2024-09-30 --candidate-only
```

Use dates and budgets appropriate to the reviewed dataset. The current trainer
implements one explicit chronological fold with label-end purging and independent
sigmoid calibration; it marks results `research`, not `validated`. Three-fold
promotion evidence, full-market throughput and automatic champion replacement
are not claimed. Published feature snapshots must match the inference price basis.

### Historical Research And Data Readiness

Free-source backfills can now enter a separately identified `price_index_v1`
experiment, never the default industry/PIT specification. This mode requires
`--mode historical_research --feature-set price_index_v1 --candidate-only`.
It refuses activation and does not emit trusted online feature snapshots.
Source trust flags remain unchanged. A successful experiment is not a PIT backtest.

Run from the repository root using the backend's installed Python environment:

```sh
python -m stock_forecaster.research.cli --root .local/research-experiment bootstrap --limit 30 --index-limit 4 --start 2021-09-01 --end 2026-09-04
python -m stock_forecaster.research.cli --root .local/research-experiment prepare-dataset --collection-report COLLECTION_REPORT_ID --benchmark 000300.SS
python -m stock_forecaster.research.cli --root .local/research-experiment readiness --dataset DATASET_ID
python -m stock_forecaster.research.cli --root .local/research-experiment train --dataset DATASET_ID --mode historical_research --feature-set price_index_v1 --candidate-only --train-end 2024-06-06 --validation-end 2025-03-10 --calibration-end 2025-12-04 --test-end 2026-09-04
```

Use IDs returned by each preceding command and `split_ends` from the readiness
report, or provide all four chronological ends explicitly. The defaults allocate
55/15/15/15 percent of sessions before label-end purging; this is an experiment
split, not statistical validation. The loader and readiness command retain the
250,000-row and 512 MiB data guards; they are not process RSS limits.

Readiness checks complete 21-session feature windows, explicit-calendar labels,
purged partition sizes and up/non-up counts. Missing or zero-activity stock bars
invalidate affected windows and labels, never forward-fill. Strict training
rejects invalid full bars. TimesFM/GARCH context counts check positive close and
available activity fields separately from OHLC features; they indicate input
eligibility, not successful model fitting or prediction quality. Read the JSON
`ready` flags: a successfully generated report can still contain blockers.

Successful provider calls retain the original SDK DataFrame as `provider_response`
snapshots, including rows rejected during normalization. These are not HTTP wire
responses. Content/request hashes preserve the earliest local observation of an
identical payload; each retrieval also records observation and ingestion time.
Unknown publication timestamps remain null. This is payload-level revision
tracking, not a reconstructed historical publication archive or a per-row change log.

On 2026-09-05 a bounded real run trained all six LightGBM heads on 35,589 feature
rows, but return MAE did not beat the zero-return baseline at any horizon. The
sample is the first 30 current SZ symbols, not a representative/full-market PIT
universe. A GARCH candidate also fitted successfully; no candidate was activated.
See [the measured readiness report](../../docs/research/2026-09-05-data-readiness.md)
for exact snapshot IDs, metrics and limitations. Existing containers must be rebuilt
explicitly to gain these commands; this verification used local Python and did not
restart services, enable schedules, buy data or add dependencies.

### Local Research Web Preview

The workspace has two audience groups and six separate pages:

| Group | Pages |
| --- | --- |
| User Viewing | Market quotes (first/default), stock prediction |
| Research Management | Forecast experiment, historical backtest, model evaluation, data status |

Desktop displays the two sidebar groups. Narrow screens use a workspace selector
and only that group's bottom navigation, outside the scrolling content. Only one
page is visible. Market quotes are no longer nested inside research. Forecast
experiments and backtests have separate state, compact parameter/result layouts,
and explicit run commands. Forecast charts and detail rows switch locally instead
of stacking three charts; CSV export uses the retained response.

Views mount on first visit and retain their state during navigation. Market and
stock-prediction pages share the selected security. Forecast and backtest pages
retain their own parameters and results independently, along with in-flight work.
Results identify the submitted parameters and warn when the form has since changed.
Hidden quote/health polling pauses; submitted prediction jobs can still finish.
Hash links (`#market`, `#prediction`, `#forecast`, `#backtest`, `#models`, `#data`)
support direct links and browser back/forward; old `#research` links normalize to
`#forecast` without adding a history entry. State retention is within the current page session, not a
promise to persist unsaved parameters across browser reloads.

These groups are information architecture, not authentication or authorization.
No administrator login or privilege enforcement is claimed. See
[the audience and authorization design](../../docs/research/2026-09-06-audience-design.md)
for the planned user/researcher/administrator boundaries.

`GET /api/v2/evidence` returns allowlisted, offline summaries; it never collects,
trains, loads a model or creates a task. Training and readiness reports must refer
to the same dataset IDs. The latest source probe is displayed separately; missing
source or matching readiness evidence produces a partial response, not approval.
`production_ready` remains false even when all report types are available.

For an isolated preview that leaves the existing 5174/8012 containers unchanged,
use the installed backend Python environment from the repository root:

```sh
python -m stock_forecaster.research.preview seed --root .local/web-preview-new --source .local/data-readiness-20260905 --source .local/source-check-20260906
python -m stock_forecaster.research.preview serve --root .local/web-preview-new --port 8013 --web-port 5175
```

In another terminal:

```sh
npm --prefix apps/stock_forecaster/frontend run --silent dev -- --host 127.0.0.1 --port 5175 --strictPort --mode research-preview
```

Open `http://127.0.0.1:5175/` and select **data status** or **model evaluation** in
the navigation. Data coverage and source checks are separate from candidate metrics.
The latest three-source instance uses `.local/web-preview-free-sources-20260906`;
the prior four-report snapshot remains `.local/web-preview-20260906-verified`. `seed` requires
a new destination and refuses to overwrite one. It copies only report snapshots,
preserving their hashes and original timestamps; it does not copy raw provider
frames, jobs or model directories. Known sensitive field names/credential strings
are rejected, but this is a guard, not comprehensive secret detection. Inspect
reports before sharing local files. The HTTP response always uses a field allowlist.

The preview backend binds only to 127.0.0.1 and registers GET health/evidence
endpoints only. The frontend uses a same-origin Vite proxy: evidence to 8013,
other `/api` requests to existing 8012. Predictions still use the existing worker
and model state. The preview does not promote experimental LightGBM artifacts.
In other environments `VITE_EVIDENCE_API_BASE_URL` can override the default common
API origin; configure CORS there explicitly.

`research-preview` is a development-server mode, not a production reverse proxy.
Do not expose Vite or the existing unauthenticated research APIs publicly. The
source/report snapshot is static until explicitly reseeded into a new preview
directory. Windows/WSL and both terminals must stay running for this preview;
automatic startup, authentication and TLS are not installed by these commands.
See [the release decision and staged plan](../../docs/research/2026-09-06-web-release.md).

Backup to a mounted directory on another disk, outside `/data`:

```sh
docker compose run --rm -v /path/on/another/disk:/backup research-worker \
  stock-forecaster-research backup /backup/research-backup-2026-09-05
```

The destination must not exist. Restore the complete backup directory into a new
data volume, preserving read/write ownership for UID 10001. Recreate internal
credentials separately. Replay and scoring use frozen predictions rather than
claiming a new neural-network run is bit-identical across hardware.

### API And Validation Boundaries

`GET /api/v2/symbols`, `POST /api/v2/predictions`, `GET /api/v2/jobs/{id}`,
`DELETE /api/v2/jobs/{id}`, and `GET /api/v2/predictions/{ticker}/latest` drive
the workspace. Submission accepts an optional `Idempotency-Key`. Cancellation
of running work is cooperative at safe boundaries, not a forced native-library kill.
`GET /api/v2/reports/latest` exposes collection/execution counts separately.
The internal authenticated batch endpoint supports up to eight 32-512 point
contexts and 1-20 steps. Daily tasks currently use individual jobs; a successful
batch smoke does not imply the daily scheduler already groups them.

On 2026-09-05 the container probe retrieved a current **5,556-stock** universe
and complete raw/qfq OHLCV/amount for two stocks plus raw bars for two indices.
The historical industry download succeeded, but publication timing could not be
verified. Therefore `historical_industry_publication_unverified` and
`historical_price_vintage_unverified` remain explicit blockers for full historical
LightGBM acceptance. These probes do not prove historical PIT universe coverage.

Engineering tests include native small-model training, real storage and browser
fixtures. They do not prove investment performance. Due-forecast scoring compares
returns and risk with independent baselines, requires reconciled price history,
and separates prospective from replayed predictions. Twenty-session outcomes only
exist after their target sessions have actually completed.

See [the implementation plan](../../docs/superpowers/plans/2026-09-05-prediction-research.md)
and [frontend testing](frontend/TESTING.md). The sections below describe the
retained v1 research interface and its limitations.

An unofficial, research-only full-stack demonstration of stock time-series
forecasting with the current TimesFM 3.0 PyTorch API. It does not recommend
Buy, Sell, or Hold actions and does not connect to brokerages.

> **Financial disclaimer:** Forecasts are uncertain and are not financial or
> investment advice. Historical evaluation does not imply future performance
> or profitability.
>
> **Weights license:** TimesFM source code is Apache-2.0, but the TimesFM 3.0
> pretrained weights are currently distributed under the separate
> `timesfm-non-commercial-license-v1.0`. The default weights are restricted to
> non-commercial, non-production use.

## Architecture

- `backend/`: FastAPI routes, typed configuration, A-share/yfinance providers/cache,
  feature preparation, TimesFM adapter, forecasting, backtesting, schemas, and
  offline pytest tests.
- `frontend/`: strict React/TypeScript Vite prediction workspace, TanStack Query state,
  Plotly charts, CSV export, and Vitest/Testing Library tests.
- `docker-compose.yml`: optional CPU-first local stack. Model weights are never
  baked into either image.

The market-data providers and TimesFM adapter are interfaces and all tests use
offline fixtures. API routes and UI components orchestrate focused service modules.

## China A-share sources

The dashboard defaults to `600519` (Kweichow Moutai). It accepts six-digit
stock codes, exchange prefixes such as `sh600519` / `sz000001` / `bj920001`,
and suffixes such as `600519.SS`, `600519.SH`, `000001.SZ`, `920001.BJ`.
Bare `000001` means the Shenzhen stock, not the Shanghai index.
Overseas symbols such as `SPY` still use yfinance and do not request A-share quotes.

- **Tencent** (`qt.gtimg.cn`): primary snapshot, five bid/ask levels, PE, PB,
  turnover rate, float market capitalization and total market capitalization.
- **Sina** (`hq.sinajs.cn`): automatic snapshot/order-book fallback, with the
  required Referer header. Missing valuation fields remain null, not zero.
- **AKShare 1.18.94**: `stock_zh_a_hist_tx(adjust="qfq")` supplies Tencent
  forward-adjusted daily prices to the existing TimesFM forecast/backtest path.
  History carries `source: "akshare/tencent"` and currency `CNY`.

Quotes are GBK-decoded server-side. Volume and book sizes are normalized to
**shares**; amount and capitalization are in **CNY**. Tencent volume/amount
may be rounded by the upstream. PE is the upstream PE field, not a guaranteed
TTM measure. The UI shows source and upstream timestamp in UTC+8; a weekend,
holiday, suspended stock or delayed source can return an older snapshot.

The visible page polls every 10 seconds; polling can be paused and the source
selected explicitly. Auto mode tries Tencent then Sina. Explicit source mode
does not silently switch. Successful quotes are cached for 5 seconds, with
same-key concurrent requests combined. Snapshot failure never runs or blocks
the model, and stale data is not represented as a successful refresh.

Only historical daily prices feed the model: snapshots, five-level quotes,
PE/PB and volume are **not** added as model covariates. The history end date is
exclusive. Today's bar is excluded until 16:00 Asia/Shanghai, allowing a margin
after close. AKShare runs in a disposable subprocess with a 45-second total
deadline, including its internal requests that lack individual timeouts.
Historical adjustments may be revised by the source; this is not a
point-in-time corporate-action database or a production trading feed.

## 中文界面与大盘预测

页面、行情、图表工具栏、报错提示、回测结果和表格导出均以中文显示。
导出的表格使用带字节序标记的 UTF-8 编码，便于在中文电子表格软件中打开。

“个股／大盘指数”可切换预测标的。指数使用未复权收盘点位，不应用个股复权，
不显示五档盘口和个股估值。六个预设指数如下：

| 指数 | 接口代码 |
| --- | --- |
| 上证指数 | `000001.SS` / `sh000001` |
| 深证成指 | `399001.SZ` / `sz399001` |
| 创业板指 | `399006.SZ` / `sz399006` |
| 沪深300 | `000300.SS` / `sh000300` |
| 中证500 | `000905.SS` / `sh000905` |
| 科创50 | `000688.SS` / `sh000688` |

`000001` 不带市场标识时仍表示平安银行，不能用于表示上证指数。
历史和快照响应新增 `instrument_type`（`stock` / `index`）；历史响应还提供
指数 `name`。指数单位为点，个股价格单位按币种显示。

### 预测周期

| 类型 | 预设预测跨度 |
| --- | --- |
| 超短期 | 1、3 个交易日 |
| 短期 | 5、10 个交易日 |
| 中期 | 20、60 个交易日 |

可自定义 1 至 60 日。每一步仍使用一个完整日线样本，因此这些选项是
**未来预测跨度**，不是 1 分钟或 5 分钟的采样频率。每日预测值、涨跌幅和近似
区间显示在逐日结果表中；回测使用同一预测跨度。周期名称仅为界面分组，
不代表投资策略或准确率保证。

中国个股及上述指数的未来日期使用 `exchange-calendars` 的上海交易日历，
跳过周末与已发布休市日。当前固定版本 4.13.2 覆盖至 **2026-12-31**；请求
跨越已知范围会返回 `calendar_unavailable`，而不会猜测未来节假日。
海外证券仍按工作日排期，并显示中文限制提示。盘中分钟预测不在本次范围内。

### Windows local ports

Use the configured Python interpreter that has the backend and TimesFM installed.
For this workspace, keep ports 8000/8001 free for other applications and start:

```powershell
python -m uvicorn stock_forecaster.main:app --host 127.0.0.1 --port 8010
```

In a second terminal, from the repository root:

```powershell
$env:VITE_API_BASE_URL = "http://localhost:8010"
npm --prefix apps/stock_forecaster/frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open <http://localhost:5173>; API documentation is at <http://localhost:8010/docs>.
The local, ignored `frontend/.env.development.local` also retains this API URL.
Select a free port if these ports are already occupied; do not stop unrelated services.

## Local setup

Python 3.10+ and Node.js 22+ are recommended. From the repository root:

```bash
cd apps/stock_forecaster/backend
python -m venv .venv
source .venv/bin/activate
# Optional CPU-only wheel first; omit for your platform's default Torch wheel.
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e "../../../[torch]"
python -m pip install -e ".[dev]"
cp ../.env.example .env
uvicorn stock_forecaster.main:app --reload --port 8000
```

In another terminal:

```bash
cd apps/stock_forecaster/frontend
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev -- --port 5173
```

Open <http://localhost:5173>. The health endpoint does not load the model.
The first forecast downloads the configured checkpoint from Hugging Face and
can take several minutes. Review the checkpoint model card for current disk,
RAM, VRAM, and license requirements before downloading. CPU inference works
but can be slow; CUDA requires a compatible PyTorch/CUDA installation.

## Configuration

Copy `.env.example` and set backend variables with the
`STOCK_FORECASTER_` prefix:

| Variable | Purpose |
| --- | --- |
| `CHECKPOINT` | Trusted server-side checkpoint; clients cannot override it |
| `DEVICE` | Health/default device preference: `auto`, `cpu`, or `cuda` |
| `CORS_ORIGINS` | JSON list or comma-separated allowed frontend origins |
| `CACHE_TTL_SECONDS`, `CACHE_MAX_ENTRIES` | Successful market-data cache lifetime and bound |
| `MIN_CONTEXT_LENGTH`, `DEFAULT_CONTEXT_LENGTH`, `MAX_CONTEXT_LENGTH` | Context policy |
| `MAX_HORIZON` | Forecast horizon policy |
| `MODEL_ENABLED` | Disable all model use while retaining health/data APIs |
| `MAX_CONCURRENT_INFERENCES` | Bounded synchronous inference capacity |
| `LOG_LEVEL` | Safe application log level |

`VITE_API_BASE_URL` configures the frontend. Never commit `.env`, credentials,
downloaded data, model weights, caches, or exported CSV files.

## API

- `GET /api/v1/health` returns version, selected device, and model state without
  loading TimesFM.
- `GET /api/v1/quotes/{ticker}?source=auto` returns an A-share snapshot and five
  levels. `source` also accepts `tencent` and `sina`.
- `GET /api/v1/market-data/{ticker}` accepts a supported `period`, or `start`
  and `end`, plus `include_volume`.
- `POST /api/v1/forecasts` accepts ticker/range, horizon, context, target, and
  device. The checkpoint always comes from server configuration.
- `POST /api/v1/backtests` performs bounded chronological walk-forward
  evaluation against zero-return, historical-mean-return, and random-walk
  price baselines.

Errors use `{ "error": { "code", "message", "details?", "request_id" } }`.
Swagger documentation is available at <http://localhost:8000/docs>.

## Forecast semantics and safeguards

The recommended/default target is:

```text
log_return_t = log(price_t / price_{t-1})
```

Prices must be positive and finite. The backend never forward-fills target
prices, keeps dates separate, creates contiguous float32 contexts, requires at
least 32 valid target observations, and validates all model output shapes and
values. TimesFM 3.0 returns exactly q0.1 through q0.9; unlike the older 2.5
convention, there is no mean column at quantile index zero.
Requests may use the API limit of 16,384 context values; the current TimesFM
3 implementation consumes at most 15,360 and the response warns when it
truncates a longer request.

The approximate price path is:

```text
future_price_t = last_price * exp(cumulative_predicted_log_returns_t)
```

Independently accumulated marginal quantiles are approximate paths, not joint
path confidence intervals. Chinese-market dates use the published XSHG trading
calendar; overseas dates still use weekdays and may omit exchange holidays.

Backtests only pass returns strictly before each forecast origin to TimesFM.
They use chronological windows with no random split and report return
MAE/RMSE, directional accuracy, price MAE, q10-q90 coverage, and interval
width. There is intentionally no portfolio, trading, profit, fee, or slippage
simulation.

## Tests and verification

All automated tests are offline, CPU-only, and load neither upstream market data
nor TimesFM weights:

```bash
python -m pytest apps/stock_forecaster/backend/tests -q
ruff check apps/stock_forecaster/backend
python -m compileall apps/stock_forecaster/backend/src

cd apps/stock_forecaster/frontend
npm ci
npm run typecheck
npm run lint
npm run test -- --run
npm run build
```

To manually verify real inference after accepting the weights license:

1. Complete the backend setup above with network access and sufficient disk/RAM.
2. Run `curl http://localhost:8000/api/v1/health`; confirm `not_loaded`.
3. Fetch data with
   `curl 'http://localhost:8000/api/v1/market-data/SPY?period=2y'`.
4. POST a CPU forecast:

   ```bash
   curl -X POST http://localhost:8000/api/v1/forecasts \
     -H 'Content-Type: application/json' \
     -d '{"ticker":"SPY","period":"2y","horizon":5,"context_length":512,"target":"log_return","device":"cpu"}'
   ```

5. Confirm the first call downloads once, later calls reuse the loaded model,
   and the dashboard charts/summary/CSV agree with the response.

## Docker and troubleshooting

Run `docker compose up --build` from this directory. The default is CPU-only
and mounts a named Hugging Face cache. Docker is optional and is not used by
tests. CUDA containers require a host NVIDIA driver/toolkit, GPU-enabled
PyTorch image, Compose GPU reservation, and `STOCK_FORECASTER_DEVICE=cuda`;
those host-specific changes are deliberately not enabled by default.

- `model_disabled`: set `STOCK_FORECASTER_MODEL_ENABLED=true`.
- `device_unavailable`: use CPU/auto or install compatible CUDA support.
- `model_load_failed`: verify network/Hugging Face access, license acceptance,
  free disk space, and checkpoint configuration.
- `market_data_unavailable`: yfinance is an unofficial upstream source and may
  throttle, time out, revise history, or omit currency/volume metadata.
- `insufficient_history`: request a longer period or smaller context.

Known limitations include delayed/revised market data, weekday-only overseas
dates, the published Chinese-calendar range, marginal rather than joint path
intervals, no corporate-event/news features, and no guarantee that TimesFM outperforms a
simple baseline. This demonstration is not production trading software.
