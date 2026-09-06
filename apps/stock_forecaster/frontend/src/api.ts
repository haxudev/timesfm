import type { EvidenceOverview } from './evidenceTypes'
import type {
  BacktestResponse,
  ForecastRequest,
  ForecastResponse,
  HealthResponse,
  QuoteResponse,
  QuoteSource,
  ResearchSymbol,
  PredictionBundle,
  PredictionHorizon,
  PredictionJob,
  SubmittedJob,
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000')
  .replace(/\/$/, '')
const EVIDENCE_BASE_URL = (import.meta.env.VITE_EVIDENCE_API_BASE_URL ?? API_BASE_URL).replace(/\/$/, '')

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
  readonly status?: number

  constructor(
    message: string,
    kind: 'validation' | 'network' | 'model' | 'server',
    code?: string,
    requestId?: string,
    status?: number,
  ) {
    super(message)
    this.kind = kind
    this.code = code
    this.requestId = requestId
    this.status = status
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 15_000,
  baseUrl = API_BASE_URL,
): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${baseUrl}${path}`, {
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
        error?.message ?? '服务器暂时无法处理请求。',
        kind,
        code,
        error?.request_id,
        response.status,
      )
    }
    return body as T
  } catch (error) {
    if (options.signal?.aborted) throw new DOMException('Request cancelled', 'AbortError')
    if (error instanceof ApiError) throw error
    const message = error instanceof DOMException && error.name === 'AbortError'
      ? '请求超时，请稍后重试。'
      : '无法连接后端服务，请检查服务是否已启动。'
    throw new ApiError(message, 'network')
  } finally {
    window.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abort)
  }
}

export const api = {
  evidence: (signal?: AbortSignal) => request<EvidenceOverview>('/api/v2/evidence', { signal }, 15_000, EVIDENCE_BASE_URL),
  health: () => request<HealthResponse>('/api/v1/health'),
  quote: (ticker: string, source: QuoteSource = 'auto', signal?: AbortSignal) => request<QuoteResponse>(
    `/api/v1/quotes/${encodeURIComponent(ticker)}?source=${source}`,
    { signal },
  ),
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
  symbols: (query: string, signal?: AbortSignal) => request<{ items: ResearchSymbol[] }>(
    `/api/v2/symbols?q=${encodeURIComponent(query)}`, { signal },
  ),
  latestPrediction: (ticker: string, signal?: AbortSignal) => request<PredictionBundle>(
    `/api/v2/predictions/${encodeURIComponent(ticker)}/latest`, { signal },
  ),
  createPrediction: (body: { ticker: string; horizon: PredictionHorizon }, signal?: AbortSignal) => request<SubmittedJob>(
    '/api/v2/predictions', { method: 'POST', body: JSON.stringify(body), signal },
  ),
  predictionJob: (id: string, signal?: AbortSignal) => request<PredictionJob>(
    `/api/v2/jobs/${encodeURIComponent(id)}`, { signal },
  ),
  cancelPrediction: (id: string, signal?: AbortSignal) => request<PredictionJob>(
    `/api/v2/jobs/${encodeURIComponent(id)}`, { method: 'DELETE', signal },
  ),
}
