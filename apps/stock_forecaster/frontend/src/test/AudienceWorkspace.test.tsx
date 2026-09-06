import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'
import { MarketPage } from '../MarketPage'
import { forecastFixture } from './fixtures'

const clients: QueryClient[] = []
let requests: Array<{ path: string; method: string }>

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  clients.push(client)
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  requests = []
  vi.stubGlobal('fetch', vi.fn((input: string, options: RequestInit = {}) => {
    const path = new URL(input).pathname
    requests.push({ path, method: options.method ?? 'GET' })
    if (path.endsWith('/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'ok', device: 'cpu', model_state: 'ready' })))
    return Promise.resolve(new Response(JSON.stringify({ error: { code: 'quote_unavailable' } }), { status: 503 }))
  }))
})

afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/') })

it('starts on market viewing with market first and separate audience navigation groups', async () => {
  mount()
  const navigation = within(screen.getByRole('navigation', { name: '主导航' }))
  expect(navigation.getAllByRole('link')[0]).toHaveAccessibleName('行情查看')
  expect(navigation.getByRole('link', { name: '行情查看' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', { name: '行情查看' })).toBeVisible()
  expect(navigation.getByText('用户查看')).toBeVisible()
  expect(navigation.getByText('研究管理')).toBeVisible()
  expect(navigation.getByRole('link', { name: '预测实验' })).toHaveAttribute('href', '#forecast')
  expect(navigation.getByRole('link', { name: '历史回测' })).toHaveAttribute('href', '#backtest')
  expect(screen.queryByRole('tablist', { name: '实验工具' })).not.toBeInTheDocument()
  expect(requests.some((request) => request.path.endsWith('/health') || request.path.endsWith('/evidence') || request.path.endsWith('/latest'))).toBe(false)
  expect(requests.filter((request) => request.method === 'POST')).toHaveLength(0)
})

it('uses independent forecast and backtest forms, retaining both when switching pages', async () => {
  mount()
  const user = userEvent.setup()
  await user.click(screen.getByRole('link', { name: '预测实验' }))
  expect(await screen.findByRole('heading', { name: '预测实验' })).toBeVisible()
  expect(screen.getByRole('button', { name: '开始预测' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始回测' })).not.toBeInTheDocument()
  await user.clear(screen.getByLabelText('上下文长度'))
  await user.type(screen.getByLabelText('上下文长度'), '256')
  await user.click(screen.getByRole('link', { name: '历史回测' }))
  expect(await screen.findByRole('heading', { name: '历史回测' })).toBeVisible()
  expect(screen.getByRole('spinbutton', { name: '上下文长度' })).toHaveValue(512)
  await user.clear(screen.getByLabelText('回测窗口数'))
  await user.type(screen.getByLabelText('回测窗口数'), '12')
  await user.click(screen.getByRole('link', { name: '预测实验' }))
  expect(screen.getByRole('spinbutton', { name: '上下文长度' })).toHaveValue(256)
  await user.click(screen.getByRole('link', { name: '历史回测' }))
  expect(screen.getByLabelText('回测窗口数')).toHaveValue(12)
  expect(requests.filter((request) => request.method === 'POST')).toHaveLength(0)
})

it.each(['#forecast', '#research'])('opens the forecast page directly via %s without market requests', async (hash) => {
  window.history.replaceState(null, '', '/' + hash)
  mount()
  expect(await screen.findByRole('heading', { name: '预测实验' })).toBeVisible()
  expect(screen.getByRole('link', { name: '预测实验' })).toHaveAttribute('aria-current', 'page')
  expect(requests.some((request) => request.path.includes('/quotes/'))).toBe(false)
  await waitFor(() => expect(window.location.hash).toBe('#forecast'))
})

it('restores the latest stock after externally selected stock and index changes', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const selectedStock = { ticker: '000001.SZ', name: '平安银行', instrument_type: 'stock' as const }
  const onSelect = vi.fn()
  const page = (symbol: Parameters<typeof MarketPage>[0]['selected']) => <QueryClientProvider client={client}><MarketPage selected={symbol} onSelect={onSelect} /></QueryClientProvider>
  const view = render(page({ ticker: '600519.SS', name: null, instrument_type: 'stock' }))
  view.rerender(page(selectedStock))
  view.rerender(page({ ticker: '000300.SS', name: null, instrument_type: 'index' }))
  await userEvent.setup().click(screen.getByRole('radio', { name: '个股' }))
  expect(onSelect).toHaveBeenLastCalledWith(selectedStock)
})

it('finishes an in-flight experiment while another independent page is open', async () => {
  window.history.replaceState(null, '', '/#forecast')
  const original = globalThis.fetch
  let finish!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn((input: string, options: RequestInit = {}) => {
    if (new URL(input).pathname.endsWith('/forecasts')) {
      requests.push({ path: '/api/v1/forecasts', method: options.method ?? 'GET' })
      return new Promise<Response>((resolve) => { finish = resolve })
    }
    return original(input, options)
  }))
  mount()
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '开始预测' }))
  expect(await screen.findByRole('button', { name: '正在预测…' })).toBeDisabled()
  await user.click(screen.getByRole('link', { name: '历史回测' }))
  expect(screen.getByRole('button', { name: '开始回测' })).toBeEnabled()
  await act(async () => finish(new Response(JSON.stringify(forecastFixture))))
  await user.click(screen.getByRole('link', { name: '预测实验' }))
  expect(await screen.findByRole('region', { name: '预测结果' })).toBeVisible()
  expect(requests.filter((request) => request.method === 'POST')).toHaveLength(1)
})