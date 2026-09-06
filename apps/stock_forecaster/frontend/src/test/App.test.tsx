import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { MarketPage } from '../MarketPage'
import { ForecastExperimentPage, HistoricalBacktestPage } from '../ResearchPages'
import type { ResearchSymbol } from '../types'
import { api, ApiError } from '../api'
import { backtestFixture, forecastFixture } from './fixtures'

vi.mock('../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api')>()
  return {
    ...original,
    api: {
      health: vi.fn(),
      quote: vi.fn(),
      forecast: vi.fn(),
      backtest: vi.fn(),
    },
  }
})

const mockedApi = vi.mocked(api)

function MarketHarness() {
  const [selected, setSelected] = useState<ResearchSymbol>({ ticker: '600519', name: null, instrument_type: 'stock' })
  return <MarketPage selected={selected} onSelect={setSelected} />
}

function renderApp(page: 'market' | 'forecast' | 'backtest' = 'forecast') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      {page === 'market' ? <MarketHarness /> : page === 'backtest' ? <HistoricalBacktestPage /> : <ForecastExperimentPage />}
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApi.quote.mockResolvedValue({
    ticker: '600519.SS', name: '贵州茅台', source: 'tencent', currency: 'CNY',
    as_of: '2026-09-04T16:14:33+08:00', last: 1330, previous_close: 1298.88,
    open: 1295.88, high: 1338.86, low: 1295.60, change: 31.12, change_percent: 2.4,
    volume: 4541600, amount: 6022590000, pe_ratio: 20.42, pb_ratio: 6.62,
    market_cap: 1662609000000, float_market_cap: 1662609000000, turnover_rate: 0.36,
    bids: Array.from({ length: 5 }, () => ({ price: 1329.82, volume: 100 })),
    asks: Array.from({ length: 5 }, () => ({ price: 1330, volume: 4600 })),
    warnings: [],
  })
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

describe('extracted legacy capabilities on independent pages', () => {
  it('loads the default A-share snapshot with source, time, valuations and five levels', async () => {
    renderApp('market')
    expect(await screen.findByText('贵州茅台')).toBeVisible()
    expect(screen.getByText('20.42')).toBeVisible()
    expect(screen.getByText('6.62')).toBeVisible()
    expect(screen.getByText(/2026-09-04/)).toBeVisible()
    expect(screen.getByRole('table', { name: '五档盘口（股）' })).toBeVisible()
    expect(mockedApi.quote).toHaveBeenCalledWith('600519', 'auto')
    expect(mockedApi.forecast).not.toHaveBeenCalled()
  })

  it('switches quote sources without running the forecast', async () => {
    const user = userEvent.setup()
    renderApp('market')
    await screen.findByText('贵州茅台')
    expect(screen.getByRole('option', { name: 'TX' })).toHaveValue('tencent')
    expect(screen.getByRole('option', { name: 'XL' })).toHaveValue('sina')
    await user.selectOptions(screen.getByLabelText('行情来源'), 'sina')
    await waitFor(() => expect(mockedApi.quote).toHaveBeenCalledWith('600519', 'sina'))
    expect(mockedApi.forecast).not.toHaveBeenCalled()
  })

  it('keeps quote failures on the read-only market page', async () => {
    mockedApi.quote.mockRejectedValue(new ApiError('Quote upstream unavailable', 'server', 'quote_unavailable'))
    renderApp('market')
    expect(await screen.findByRole('alert')).toHaveTextContent('行情暂不可用')
    expect(screen.queryByRole('button', { name: '开始预测' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看预测' })).toBeVisible()
  })

  it('shows research scope without exposing model configuration secrets', async () => {
    renderApp()
    expect(screen.getByRole('heading', { name: '预测实验' })).toBeVisible()
    expect(screen.getByText(/不构成投资建议/)).toBeVisible()
    expect(screen.queryByText(/模型许可|仅限非商业、非生产用途/)).not.toBeInTheDocument()
  })

  it('validates the form and constructs a normalized request', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    renderApp()
    const horizon = screen.getByLabelText('预测天数')
    fireEvent.change(horizon, { target: { value: '61' } })
    await user.click(screen.getByRole('button', { name: '开始预测' }))
    expect(screen.getByRole('alert')).toHaveTextContent('预测天数必须为 1 至 60')
    expect(mockedApi.forecast).not.toHaveBeenCalled()

    fireEvent.change(horizon, { target: { value: '5' } })
    await user.clear(screen.getByLabelText('股票代码'))
    await user.type(screen.getByLabelText('股票代码'), ' spy ')
    await user.click(screen.getByRole('button', { name: '开始预测' }))
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
    await user.click(screen.getByRole('button', { name: '开始预测' }))
    expect(screen.getByRole('button', { name: '正在预测…' })).toBeDisabled()
  })

  it('renders forecast summaries and correctly named charts', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    renderApp()
    await user.click(screen.getByRole('button', { name: '开始预测' }))
    expect((await screen.findAllByText('101.00'))[0]).toBeVisible()
    expect(screen.getByText(/时序模型 · 处理器/)).toBeVisible()
    expect(screen.getAllByText('1.00%')[0]).toBeVisible()
    expect(screen.getByRole('img', { name: '预测走势与近似区间' })).toBeVisible()
    expect(screen.queryByRole('img', { name: '历史走势' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: '历史行情' }))
    expect(screen.getByRole('img', { name: '历史走势' })).toBeVisible()
    await user.click(screen.getByRole('radio', { name: '收益分布' }))
    const returnsChart = screen.getByRole('img', { name: '预测对数收益率' })
    const series = JSON.parse(returnsChart.dataset.series ?? '[]') as Array<{
      y: number[]
    }>
    expect(series[0].y).toEqual([0.03])
    expect(series[1].y).toEqual([-0.02])
  })

  it('renders differentiated model errors', async () => {
    const user = userEvent.setup()
    mockedApi.forecast.mockRejectedValue(
      new ApiError('CUDA was requested but is not available.', 'model', 'device_unavailable'),
    )
    renderApp()
    await user.click(screen.getByRole('button', { name: '开始预测' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '显卡计算不可用',
    )
  })

  it('renders independent backtest comparisons', async () => {
    const user = userEvent.setup()
    mockedApi.backtest.mockResolvedValue(backtestFixture)
    renderApp('backtest')
    await user.click(screen.getByRole('button', { name: '开始回测' }))
    expect(await screen.findByText('零收益基线')).toBeVisible()
    expect(screen.getByText('时序模型', { exact: true })).toBeVisible()
    expect(screen.getByText('历史均值基线')).toBeVisible()
    expect(screen.getByText('随机游走基线')).toBeVisible()
    expect(screen.getByText(/历史回测不代表未来表现/)).toBeVisible()
  })

  it.each(['forecast', 'backtest'] as const)('identifies the submitted parameters when the %s form changes', async (kind) => {
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    mockedApi.backtest.mockResolvedValue(backtestFixture)
    renderApp(kind)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: kind === 'forecast' ? '开始预测' : '开始回测' }))
    expect(await screen.findByLabelText('结果参数')).toHaveTextContent('600519')
    await user.clear(screen.getByLabelText('股票代码'))
    await user.type(screen.getByLabelText('股票代码'), '002594')
    expect(screen.getByText('参数已变更，当前结果仍对应上次提交。')).toBeVisible()
    expect(screen.getByLabelText('结果参数')).toHaveTextContent('600519')
    expect(screen.getByLabelText('结果参数')).not.toHaveTextContent('002594')
    expect(kind === 'forecast' ? mockedApi.forecast : mockedApi.backtest).toHaveBeenCalledTimes(1)
  })

  it('exposes custom horizons as an explicit radio choice', async () => {
    renderApp()
    const user = userEvent.setup()
    fireEvent.change(screen.getByLabelText('预测天数'), { target: { value: '7' } })
    expect(screen.getByRole('radio', { name: '自定义' })).toBeChecked()
    expect(screen.getByRole('radiogroup', { name: '预测期限' })).toBeVisible()
    await user.click(screen.getByRole('radio', { name: '5 日' }))
    expect(screen.getByLabelText('预测天数')).toHaveValue(5)
  })

  it.each([1, 3, 5, 10, 20, 60])('submits the selected %i-day index forecast', async (horizon) => {
    const user = userEvent.setup()
    mockedApi.forecast.mockResolvedValue(forecastFixture)
    renderApp()
    await user.click(screen.getByRole('radio', { name: '大盘指数' }))
    expect(screen.getByRole('combobox', { name: '指数' })).toHaveValue('000001.SS')
    await user.click(screen.getByRole('radio', { name: `${horizon} 日` }))
    expect(screen.getByLabelText('预测天数')).toHaveValue(horizon)
    await user.click(screen.getByRole('button', { name: '开始预测' }))
    await waitFor(() => expect(mockedApi.forecast).toHaveBeenCalledWith(
      expect.objectContaining({ ticker: '000001.SS', horizon }), expect.any(Object),
    ))
  })

  it('shows index points without a stock order book and preserves the stock selection', async () => {
    const user = userEvent.setup()
    const quote = await mockedApi.quote('600519', 'auto')
    mockedApi.quote.mockImplementation(async (ticker) => ticker === '600519' ? quote : ({
      ...quote, ticker, name: '沪深300', instrument_type: 'index', bids: [], asks: [],
    }))
    renderApp('market')
    await user.click(screen.getByRole('radio', { name: '大盘指数' }))
    await user.selectOptions(screen.getByLabelText('指数'), '000300.SS')
    await waitFor(() => expect(screen.getByRole('heading', { name: '沪深300' })).toBeVisible())
    expect(screen.queryByRole('table', { name: '五档盘口（股）' })).not.toBeInTheDocument()
    expect(screen.getByText('点')).toBeVisible()
    await user.click(screen.getByRole('radio', { name: '个股' }))
    expect(await screen.findByRole('heading', { name: '贵州茅台' })).toBeVisible()
    expect(await screen.findByRole('table', { name: '五档盘口（股）' })).toBeVisible()
  })
})
