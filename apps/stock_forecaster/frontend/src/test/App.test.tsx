import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api, ApiError } from '../api'
import { backtestFixture, forecastFixture } from './fixtures'

vi.mock('../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api')>()
  return {
    ...original,
    api: {
      health: vi.fn(),
      forecast: vi.fn(),
      backtest: vi.fn(),
    },
  }
})

const mockedApi = vi.mocked(api)

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApi.health.mockResolvedValue({
    status: 'ok',
    device: 'cpu',
    version: '0.1.0',
    model_state: 'not_loaded',
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('stock forecaster dashboard', () => {
  it('shows the financial and model license notices', async () => {
    renderApp()
    expect(screen.getByText(/Not financial or investment advice/i)).toBeVisible()
    expect(screen.getByText(/non-commercial,\s*non-production use/i)).toBeVisible()
  })

  it('validates the form and constructs a normalized request', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    renderApp()
    const horizon = screen.getByLabelText('Horizon')
    fireEvent.change(horizon, { target: { value: '61' } })
    await user.click(screen.getByRole('button', { name: 'Run forecast' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Horizon must be 1–60')
    expect(mockedApi.forecast).not.toHaveBeenCalled()

    fireEvent.change(horizon, { target: { value: '5' } })
    await user.clear(screen.getByLabelText('Ticker'))
    await user.type(screen.getByLabelText('Ticker'), ' spy ')
    await user.click(screen.getByRole('button', { name: 'Run forecast' }))
    await waitFor(() => expect(mockedApi.forecast).toHaveBeenCalledWith(
      expect.objectContaining({
        ticker: 'SPY',
        period: '2y',
        horizon: 5,
        context_length: 512,
        target: 'log_return',
        device: 'auto',
      }),
      expect.any(Object),
    ))
  })

  it('disables the forecast action while loading', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockReturnValue(new Promise(() => undefined))
    renderApp()
    await user.click(screen.getByRole('button', { name: 'Run forecast' }))
    expect(screen.getByRole('button', { name: 'Running forecast…' })).toBeDisabled()
  })

  it('renders forecast summaries and correctly named charts', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    renderApp()
    await user.click(screen.getByRole('button', { name: 'Run forecast' }))
    expect(await screen.findByText('101.00')).toBeVisible()
    expect(screen.getByText('1.00%')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Historical price' })).toBeVisible()
    expect(screen.getByRole('img', { name: 'Approximate forecast price path' })).toBeVisible()
    const returnsChart = screen.getByRole('img', { name: 'Forecast log returns' })
    const series = JSON.parse(returnsChart.dataset.series ?? '[]') as Array<{
      y: number[]
    }>
    expect(series[0].y).toEqual([0.03])
    expect(series[1].y).toEqual([-0.02])
  })

  it('renders differentiated model errors', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockRejectedValue(
      new ApiError('CUDA was requested but is not available.', 'model'),
    )
    renderApp()
    await user.click(screen.getByRole('button', { name: 'Run forecast' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'model: CUDA was requested but is not available.',
    )
  })

  it('renders independent backtest comparisons', async () => {
    const user = userEvent.setup()
    mockedApi.backtest.mockResolvedValue(backtestFixture)
    renderApp()
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByText('zero return')).toBeVisible()
    expect(screen.getByText('historical mean')).toBeVisible()
    expect(screen.getByText('random walk')).toBeVisible()
    expect(screen.getByText(/does not imply future performance/i)).toBeVisible()
  })
})
