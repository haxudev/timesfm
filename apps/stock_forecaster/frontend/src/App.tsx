import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiError } from './api'
import {
  ForecastPriceChart,
  ForecastReturnChart,
  HistoricalChart,
} from './Charts'
import { downloadForecastCsv } from './csv'
import type {
  BacktestResponse,
  Device,
  ForecastRequest,
  Target,
} from './types'
import './styles.css'

const periods = ['1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'max']

interface FormState {
  ticker: string
  rangeMode: 'period' | 'dates'
  period: string
  start: string
  end: string
  horizon: number
  contextLength: number
  target: Target
  device: Device
}

const initialForm: FormState = {
  ticker: 'SPY',
  rangeMode: 'period',
  period: '2y',
  start: '',
  end: '',
  horizon: 5,
  contextLength: 512,
  target: 'log_return',
  device: 'auto',
}

function validate(form: FormState): string | null {
  if (!/^[A-Za-z0-9^][A-Za-z0-9.^=-]{0,14}$/.test(form.ticker.trim())) {
    return 'Enter a valid ticker.'
  }
  if (form.horizon < 1 || form.horizon > 60) return 'Horizon must be 1–60.'
  if (form.contextLength < 32 || form.contextLength > 16384) {
    return 'Context length must be 32–16,384.'
  }
  if (form.rangeMode === 'dates' && (
    !form.start || !form.end || form.start >= form.end
  )) return 'Choose a valid start and end date.'
  return null
}

function makeRequest(form: FormState): ForecastRequest {
  return {
    ticker: form.ticker.trim().toUpperCase(),
    period: form.rangeMode === 'period' ? form.period : null,
    ...(form.rangeMode === 'dates' ? { start: form.start, end: form.end } : {}),
    horizon: form.horizon,
    context_length: form.contextLength,
    target: form.target,
    device: form.device,
  }
}

function ErrorMessage({ error }: { error: Error | null }) {
  if (!error) return null
  const category = error instanceof ApiError ? error.kind : 'server'
  return <p className="error" role="alert">{category}: {error.message}</p>
}

function App() {
  const [form, setForm] = useState(initialForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [windows, setWindows] = useState(10)
  const [stepSize, setStepSize] = useState(5)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 })
  const forecast = useMutation({ mutationFn: api.forecast })
  const backtest = useMutation({
    mutationFn: (request: ForecastRequest) => api.backtest({
      ...request,
      evaluation_windows: windows,
      step_size: stepSize,
    }),
  })

  const submitForecast = (event: FormEvent) => {
    event.preventDefault()
    const error = validate(form)
    setFormError(error)
    if (!error) forecast.mutate(makeRequest(form))
  }

  const submitBacktest = (event: FormEvent) => {
    event.preventDefault()
    const error = validate(form)
      ?? (windows < 1 || windows > 100 ? 'Evaluation windows must be 1–100.' : null)
      ?? (stepSize < 1 || stepSize > 252 ? 'Step size must be 1–252.' : null)
    setFormError(error)
    if (!error) backtest.mutate(makeRequest(form))
  }

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">Research demonstration</p>
          <h1>TimesFM Stock Forecaster</h1>
          <p>Explore uncertain time-series forecasts without trading recommendations.</p>
        </div>
        <div className="health" aria-live="polite">
          <span className={health.data?.status === 'ok' ? 'dot ok' : 'dot'} />
          {health.isPending
            ? 'Checking backend…'
            : health.isError
              ? 'Backend unavailable'
              : `${health.data.model_state} · ${health.data.device}`}
        </div>
      </header>

      <section className="notice" aria-label="Important notices">
        <strong>Not financial or investment advice.</strong> Forecasts are uncertain,
        for research and education only, and do not imply profitability.
        <br />
        <strong>License:</strong> TimesFM 3.0 pretrained weights have a separate
        non-commercial license and are restricted to non-commercial,
        non-production use.
      </section>

      <form className="panel" onSubmit={submitForecast} noValidate>
        <h2>Forecast settings</h2>
        <div className="form-grid">
          <label>Ticker<input value={form.ticker} maxLength={15} onChange={(event) => setForm({ ...form, ticker: event.target.value })} /></label>
          <label>Range
            <select value={form.rangeMode} onChange={(event) => setForm({ ...form, rangeMode: event.target.value as FormState['rangeMode'] })}>
              <option value="period">Period</option>
              <option value="dates">Dates</option>
            </select>
          </label>
          {form.rangeMode === 'period' ? (
            <label>Period<select value={form.period} onChange={(event) => setForm({ ...form, period: event.target.value })}>{periods.map((period) => <option key={period}>{period}</option>)}</select></label>
          ) : (
            <>
              <label>Start<input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} /></label>
              <label>End<input type="date" value={form.end} onChange={(event) => setForm({ ...form, end: event.target.value })} /></label>
            </>
          )}
          <label>Horizon<input type="number" min={1} max={60} value={form.horizon} onChange={(event) => setForm({ ...form, horizon: event.target.valueAsNumber })} /></label>
          <label>Context length<input type="number" min={32} max={16384} value={form.contextLength} onChange={(event) => setForm({ ...form, contextLength: event.target.valueAsNumber })} /></label>
          <label>Target<select value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value as Target })}><option value="log_return">Log return</option><option value="price">Price (experimental)</option></select></label>
          <label>Device<select value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as Device })}><option value="auto">Auto</option><option value="cpu">CPU</option><option value="cuda">CUDA</option></select></label>
        </div>
        {formError && <p className="error" role="alert">{formError}</p>}
        <button type="submit" disabled={forecast.isPending}>{forecast.isPending ? 'Running forecast…' : 'Run forecast'}</button>
        <ErrorMessage error={forecast.error} />
      </form>

      {forecast.data && (
        <section aria-label="Forecast results">
          <div className="summary-grid">
            <Summary label="Latest price" value={forecast.data.summary.latest_price.toFixed(2)} />
            <Summary label="Final predicted price" value={forecast.data.summary.predicted_final_price.toFixed(2)} />
            <Summary label="Cumulative return" value={`${(forecast.data.summary.cumulative_return * 100).toFixed(2)}%`} />
            <Summary label="Approx. final interval" value={`${forecast.data.summary.final_lower_price.toFixed(2)}–${forecast.data.summary.final_upper_price.toFixed(2)}`} />
            <Summary label="Horizon / context" value={`${forecast.data.summary.horizon} / ${forecast.data.context.used_length}`} />
            <Summary label="Model" value={`${forecast.data.model.checkpoint} · ${forecast.data.model.device}`} />
          </div>
          <div className="warnings">{forecast.data.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}</div>
          <HistoricalChart data={forecast.data} />
          <ForecastPriceChart data={forecast.data} />
          <ForecastReturnChart data={forecast.data} />
          <button type="button" className="secondary" onClick={() => downloadForecastCsv(forecast.data)}>Export forecast CSV</button>
        </section>
      )}

      <form className="panel" onSubmit={submitBacktest} noValidate>
        <h2>Walk-forward backtest</h2>
        <p>Uses the forecast settings above and keeps request state independent.</p>
        <div className="form-grid">
          <label>Evaluation windows<input type="number" min={1} max={100} value={windows} onChange={(event) => setWindows(event.target.valueAsNumber)} /></label>
          <label>Step size<input type="number" min={1} max={252} value={stepSize} onChange={(event) => setStepSize(event.target.valueAsNumber)} /></label>
        </div>
        <button type="submit" disabled={backtest.isPending}>{backtest.isPending ? 'Running backtest…' : 'Run backtest'}</button>
        <ErrorMessage error={backtest.error} />
        {backtest.data && <BacktestResults data={backtest.data} />}
      </form>
    </main>
  )
}

function Summary({ label, value }: { label: string; value: string }) {
  return <article className="summary"><span>{label}</span><strong>{value}</strong></article>
}

function BacktestResults({ data }: { data: BacktestResponse }) {
  return (
    <div className="table-wrap">
      <p className="warnings">{data.warning}</p>
      <table>
        <caption>{data.window_count} windows · {data.observation_count} observations</caption>
        <thead><tr><th>Model</th><th>Return MAE</th><th>RMSE</th><th>Direction</th><th>Price MAE</th><th>q10–q90 coverage</th></tr></thead>
        <tbody>{Object.entries(data.aggregate).map(([name, metric]) => (
          <tr key={name}><th>{name.replaceAll('_', ' ')}</th><td>{metric.return_mae.toFixed(4)}</td><td>{metric.return_rmse.toFixed(4)}</td><td>{(metric.directional_accuracy * 100).toFixed(1)}%</td><td>{metric.price_mae.toFixed(2)}</td><td>{metric.interval_coverage === null ? '—' : `${(metric.interval_coverage * 100).toFixed(1)}%`}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export default App
