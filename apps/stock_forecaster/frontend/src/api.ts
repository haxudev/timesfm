import type {
  BacktestResponse,
  ForecastRequest,
  ForecastResponse,
  HealthResponse,
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000')
  .replace(/\/$/, '')

interface ErrorEnvelope {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
}

export class ApiError extends Error {
  readonly kind: 'validation' | 'network' | 'model' | 'server'
  readonly code?: string
  readonly requestId?: string

  constructor(
    message: string,
    kind: 'validation' | 'network' | 'model' | 'server',
    code?: string,
    requestId?: string,
  ) {
    super(message)
    this.kind = kind
    this.code = code
    this.requestId = requestId
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 15_000,
): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
      signal: controller.signal,
    })
    const body = await response.json() as T | ErrorEnvelope
    if (!response.ok) {
      const error = (body as ErrorEnvelope).error
      const code = error?.code
      const kind = response.status === 422
        ? 'validation'
        : code?.startsWith('model_') || code === 'device_unavailable'
          ? 'model'
          : 'server'
      throw new ApiError(
        error?.message ?? 'The server could not complete the request.',
        kind,
        code,
        error?.request_id,
      )
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const message = error instanceof DOMException && error.name === 'AbortError'
      ? 'The request timed out.'
      : 'The backend could not be reached.'
    throw new ApiError(message, 'network')
  } finally {
    window.clearTimeout(timeout)
  }
}

export const api = {
  health: () => request<HealthResponse>('/api/v1/health'),
  forecast: (body: ForecastRequest) => request<ForecastResponse>(
    '/api/v1/forecasts',
    { method: 'POST', body: JSON.stringify(body) },
    180_000,
  ),
  backtest: (body: ForecastRequest & {
    evaluation_windows: number
    step_size: number
  }) => request<BacktestResponse>(
    '/api/v1/backtests',
    { method: 'POST', body: JSON.stringify(body) },
    600_000,
  ),
}
