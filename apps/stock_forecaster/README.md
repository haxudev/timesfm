# TimesFM Stock Forecaster

## Prediction Research Workspace

The default screen is now a single-stock prediction workflow: select a stock,
choose 1/5/20 exchange sessions, and request a persisted prediction. It displays
return, calibrated upside probability, and independent cumulative volatility.
The previous v1 forecast/backtest UI remains under Advanced Research.

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

### Research Commands

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
