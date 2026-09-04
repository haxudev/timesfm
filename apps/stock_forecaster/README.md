# TimesFM Stock Forecaster

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

- `backend/`: FastAPI routes, typed configuration, yfinance provider/cache,
  feature preparation, TimesFM adapter, forecasting, backtesting, schemas, and
  offline pytest tests.
- `frontend/`: strict React/TypeScript Vite dashboard, TanStack Query state,
  Plotly charts, CSV export, and Vitest/Testing Library tests.
- `docker-compose.yml`: optional CPU-first local stack. Model weights are never
  baked into either image.

The yfinance provider and TimesFM adapter are interfaces and all tests use
fakes. API routes and UI components only orchestrate focused service modules.

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
| `CACHE_TTL_SECONDS` | Successful market-data cache lifetime |
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

The approximate price path is:

```text
future_price_t = last_price * exp(cumulative_predicted_log_returns_t)
```

Independently accumulated marginal quantiles are approximate paths, not joint
path confidence intervals. Future dates use weekdays, so exchange-specific
holidays may be missing.

Backtests only pass returns strictly before each forecast origin to TimesFM.
They use chronological windows with no random split and report return
MAE/RMSE, directional accuracy, price MAE, q10-q90 coverage, and interval
width. There is intentionally no portfolio, trading, profit, fee, or slippage
simulation.

## Tests and verification

All automated tests are offline, CPU-only, and load neither yfinance data nor
TimesFM weights:

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

Known limitations include delayed/revised yfinance data, weekday-only future
dates, marginal rather than joint path intervals, no corporate-event/news
features, no exchange calendar, and no guarantee that TimesFM outperforms a
simple baseline. This demonstration is not production trading software.
