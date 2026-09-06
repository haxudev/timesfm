import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'
import { predictionFixture } from './predictionFixtures'

const clients: QueryClient[] = []
let requests: Array<{ path: string; method: string }>
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  clients.push(client)
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

beforeEach(() => {
  window.history.replaceState(null, '', '/#prediction')
  requests = []
  vi.stubGlobal('fetch', vi.fn((input: string, options: RequestInit = {}) => {
    const path = new URL(input).pathname
    requests.push({ path, method: options.method ?? 'GET' })
    if (path.endsWith('/latest')) return Promise.resolve(response(predictionFixture()))
    if (path.endsWith('/evidence')) return Promise.resolve(response({ status: 'empty', usage: 'local_research_only', production_ready: false, source_check: null, experiment: null }))
    if (path.endsWith('/symbols')) return Promise.resolve(response({ items: [] }))
    if (path.endsWith('/health')) return Promise.resolve(response({ status: 'ok', device: 'cpu', model_state: 'ready' }))
    return Promise.resolve(response({ error: { code: 'quote_unavailable' } }, 503))
  }))
})

afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/') })

it('shows one primary view and loads reports only when visited', async () => {
  mount()
  const navigation = within(screen.getByRole('navigation', { name: '主导航' }))
  expect(navigation.getByRole('link', { name: '个股预测' })).toHaveAttribute('aria-current', 'page')
  expect(await screen.findByRole('region', { name: '个股预测' })).toBeVisible()
  expect(requests.some((item) => item.path.endsWith('/evidence'))).toBe(false)
  await userEvent.setup().click(navigation.getByRole('link', { name: '数据状态' }))
  expect(await screen.findByRole('heading', { name: '数据状态' })).toBeVisible()
  expect(screen.queryByRole('region', { name: '个股预测' })).not.toBeInTheDocument()
  expect(await screen.findByText('暂无已留存的研究报告')).toBeVisible()
  expect(requests.filter((item) => item.path.endsWith('/evidence'))).toHaveLength(1)
  await userEvent.setup().click(navigation.getByRole('link', { name: '模型评估' }))
  expect(await screen.findByRole('heading', { name: '模型评估' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: '数据状态' })).not.toBeInTheDocument()
  expect(requests.filter((item) => item.path.endsWith('/evidence'))).toHaveLength(1)
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(0)
})

it('preserves prediction horizon and results when navigating away and back', async () => {
  mount()
  await screen.findByText('测试股票甲')
  const user = userEvent.setup()
  await user.click(screen.getByRole('radio', { name: '20 日' }))
  await user.click(screen.getByRole('link', { name: '模型评估' }))
  await screen.findByRole('heading', { name: '模型评估' })
  await user.click(screen.getByRole('link', { name: '个股预测' }))
  expect(screen.getByRole('radio', { name: '20 日' })).toBeChecked()
  expect(within(screen.getByRole('group', { name: '预测累计收益' })).getByText('+8.00%')).toBeVisible()
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(0)
})

it('supports direct links and hash history changes without mounting other modules', async () => {
  window.history.replaceState(null, '', '/#models')
  mount()
  expect(await screen.findByRole('heading', { name: '模型评估' })).toBeVisible()
  expect(requests.some((item) => item.path.endsWith('/latest'))).toBe(false)
  await act(async () => {
    window.history.replaceState(null, '', '/#data')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  expect(screen.getByRole('link', { name: '数据状态' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', { name: '数据状态' })).toBeVisible()
})

it('separates experiment forms into pages and keeps parameters on return', async () => {
  mount()
  const user = userEvent.setup()
  await user.click(screen.getByRole('link', { name: '预测实验' }))
  expect(await screen.findByRole('heading', { name: '预测实验' })).toBeVisible()
  expect(screen.getByRole('button', { name: '开始预测' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始回测' })).not.toBeInTheDocument()
  const length = screen.getByLabelText('上下文长度')
  await user.clear(length)
  await user.type(length, '256')
  await user.click(screen.getByRole('link', { name: '历史回测' }))
  expect(screen.getByRole('button', { name: '开始回测' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始预测' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('link', { name: '个股预测' }))
  await user.click(screen.getByRole('link', { name: '历史回测' }))
  expect(screen.getByRole('link', { name: '历史回测' })).toHaveAttribute('aria-current', 'page')
  await user.click(screen.getByRole('link', { name: '预测实验' }))
  expect(screen.getByRole('spinbutton', { name: '上下文长度' })).toHaveValue(256)
  await waitFor(() => expect(requests.filter((item) => item.method === 'POST')).toHaveLength(0))
})

it('keeps an in-flight prediction while navigating without resubmitting or cancelling', async () => {
  let completed = false
  vi.stubGlobal('fetch', vi.fn((input: string, options: RequestInit = {}) => {
    const path = new URL(input).pathname
    requests.push({ path, method: options.method ?? 'GET' })
    if (path.endsWith('/latest')) return Promise.resolve(response({ error: { code: 'prediction_not_found' } }, 404))
    if (path === '/api/v2/predictions') return Promise.resolve(response({ job_id: 'navigation-job', status: 'pending' }, 202))
    if (path === '/api/v2/jobs/navigation-job') return Promise.resolve(response({
      id: 'navigation-job', status: completed ? 'succeeded' : 'running',
      result: completed ? predictionFixture() : null, error: null, cancellation_requested: false,
    }))
    if (path.endsWith('/evidence')) return Promise.resolve(response({ status: 'empty', source_check: null, experiment: null }))
    return Promise.resolve(response({ error: {} }, 503))
  }))
  mount()
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '生成预测' }))
  await screen.findByRole('button', { name: '取消任务' })
  await user.click(screen.getByRole('link', { name: '数据状态' }))
  completed = true
  await waitFor(() => expect(requests.filter((item) => item.path === '/api/v2/jobs/navigation-job').length).toBeGreaterThan(1), { timeout: 4000 })
  await user.click(screen.getByRole('link', { name: '个股预测' }))
  expect(await within(screen.getByRole('group', { name: '预测累计收益' })).findByText('+2.00%')).toBeVisible()
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1)
  expect(requests.filter((item) => item.method === 'DELETE')).toHaveLength(0)
})

it('keeps skip-to-content navigation inside the active destination', async () => {
  window.history.replaceState(null, '', '/#models')
  mount()
  await screen.findByRole('heading', { name: '模型评估' })
  await userEvent.setup().click(screen.getByRole('link', { name: '跳至主内容' }))
  await waitFor(() => expect(window.location.hash).toBe('#models'))
  expect(screen.getByRole('main')).toHaveFocus()
})

it('supports keyboard navigation to separate research pages without starting work', async () => {
  window.history.replaceState(null, '', '/#research')
  mount()
  const forecast = screen.getByRole('link', { name: '预测实验' })
  forecast.focus()
  const user = userEvent.setup()
  await user.keyboard('{Tab}')
  expect(screen.getByRole('link', { name: '历史回测' })).toHaveFocus()
  await user.keyboard('{Enter}')
  expect(await screen.findByRole('button', { name: '开始回测' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始预测' })).not.toBeInTheDocument()
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(0)
})