import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { partialPredictionFixture, predictionFixture } from './predictionFixtures'

const clients: QueryClient[] = []
let calls: Array<{ path: string; method: string; body: unknown; signal?: AbortSignal | null }>
let route: (path: string, init: RequestInit) => Response | Promise<Response>

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  clients.push(client)
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

function metric(name: string) { return within(screen.getByRole('group', { name })) }

beforeEach(() => {
  window.history.replaceState(null, '', '/#prediction')
  calls = []
  route = (path) => {
    if (path.includes('/symbols?')) return json({ items: [{ ticker: '000001.SZ', name: '测试股票乙', instrument_type: 'stock' }] })
    if (path.includes('/latest')) return json(predictionFixture(path.includes('000001.SZ') ? '000001.SZ' : '600519.SS', path.includes('000001.SZ') ? '测试股票乙' : '测试股票甲'))
    return json({ error: { code: 'quote_unavailable', message: 'Fixture: quote unavailable' } }, 503)
  }
  vi.stubGlobal('fetch', vi.fn((input: string, init: RequestInit = {}) => {
    const url = new URL(input)
    const path = url.pathname + url.search
    calls.push({ path, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : null, signal: init.signal })
    return Promise.resolve(route(path, init))
  }))
})

afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.unstubAllGlobals() })

describe('single-stock prediction workspace at the HTTP boundary', () => {
  it('reads the default stock without submitting, switches horizons locally, and leaves v1 unmounted', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    expect(await screen.findByRole('heading', { name: '测试股票甲' })).toBeVisible()
    expect(metric('预测累计收益').getByText('+2.00%')).toBeVisible()
    expect(metric('预测累计收益').getByText('LightGBM')).toBeVisible()
    expect(metric('上涨概率').getByText('64.0%')).toBeVisible()
    expect(metric('累计波动').getByText('3.00%')).toBeVisible()
    await user.click(screen.getByRole('radio', { name: '20 日' }))
    expect(metric('预测累计收益').getByText('+8.00%')).toBeVisible()
    expect(screen.getByText(/2026-10-09/)).toBeVisible()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    expect(calls.filter((call) => call.path.includes('/latest'))).toHaveLength(1)
    expect(screen.queryByRole('button', { name: '开始回测' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: '五档盘口（股）' })).not.toBeInTheDocument()
  })

  it('submits ticker and horizon, polls the job, then shows its completed bundle', async () => {
    const user = userEvent.setup()
    let polls = 0
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: { code: 'prediction_not_found', message: 'No prediction' } }, 404)
      if (init.method === 'POST') return json({ job_id: 'fixture-job', status: 'pending' }, 202)
      if (path === '/api/v2/jobs/fixture-job') return json(++polls === 1
        ? { id: 'fixture-job', status: 'pending', result: null, error: null }
        : { id: 'fixture-job', status: 'succeeded', result: predictionFixture(), error: null })
      return json({ error: { code: 'quote_unavailable' } }, 503)
    }
    renderWorkspace()
    expect(await screen.findByText(/暂无预测/)).toBeVisible()
    await user.click(screen.getByRole('radio', { name: '1 日' }))
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    expect(await screen.findByText(/排队中/)).toBeVisible()
    expect(screen.getByRole('button', { name: /生成预测/ })).toBeDisabled()
    expect(await metric('预测累计收益').findByText('+0.40%', {}, { timeout: 5000 })).toBeVisible()
    expect(calls.find((call) => call.method === 'POST')).toMatchObject({ path: '/api/v2/predictions', body: { ticker: '600519.SS', horizon: 1 } })
  })

  it('keeps input separate from committed ticker and ignores a late result after a keyboard stock switch', async () => {
    const user = userEvent.setup()
    let resolveOld!: (response: Response) => void
    const normal = route
    route = (path, init) => path.includes('600519.SS/latest') ? new Promise((resolve) => { resolveOld = resolve }) : normal(path, init)
    renderWorkspace()
    const search = screen.getByRole('combobox', { name: '搜索股票' })
    await user.clear(search)
    await user.type(search, '测试股票乙')
    expect(calls.some((call) => call.path.includes('000001.SZ/latest'))).toBe(false)
    await screen.findByRole('option', { name: /测试股票乙/ })
    await user.keyboard('{ArrowDown}{Enter}')
    expect(await screen.findByRole('heading', { name: '测试股票乙' })).toBeVisible()
    await act(async () => resolveOld(json(predictionFixture())))
    expect(screen.queryByRole('heading', { name: '测试股票甲' })).not.toBeInTheDocument()
    expect(calls.find((call) => call.path.includes('600519.SS/latest'))?.signal?.aborted).toBe(true)
  })

  it('labels the TimesFM fallback and never invents probability or volatility when partial', async () => {
    const bundle = partialPredictionFixture()
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    expect(await screen.findByText(/部分模型不可用/)).toBeVisible()
    expect(metric('预测累计收益').getByText('+1.00%')).toBeVisible()
    expect(metric('预测累计收益').getByText(/TimesFM/)).toBeVisible()
    expect(metric('上涨概率').getByText('--')).toBeVisible()
    expect(metric('累计波动').getByText('--')).toBeVisible()
    expect(screen.getByText('1 日：尚无可用模型制品。；5 日：尚无可用模型制品。；20 日：尚无可用模型制品。')).toBeVisible()
    expect(screen.queryByText('50.0%')).not.toBeInTheDocument()
  })

  it('does not substitute fake names or numbers when every model is unavailable', async () => {
    const bundle = partialPredictionFixture()
    bundle.name = null
    bundle.path = []
    bundle.components.timesfm = { status: 'unavailable', reason: 'fixture: model missing', artifact_id: null }
    bundle.horizons.forEach((item) => { item.timesfm_return = null })
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    expect(await screen.findByText(/暂无可用模型/)).toBeVisible()
    expect(screen.getByRole('heading', { name: '600519.SS' })).toBeVisible()
    expect(metric('预测累计收益').getByText('--')).toBeVisible()
    expect(screen.queryByText('贵州茅台')).not.toBeInTheDocument()
  })

  it('plots last 60 prices relative to the origin, a true TimesFM path and only the selected LightGBM target', async () => {
    const user = userEvent.setup()
    const bundle = predictionFixture()
    bundle.history = Array.from({ length: 65 }, (_, index) => ({
      date: new Date(Date.UTC(2026, 8, 4 - 64 + index)).toISOString().slice(0, 10), price: 36 + index,
    }))
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    const chart = await screen.findByRole('img', { name: '累计收益走势' })
    const traces = () => JSON.parse(chart.dataset.series ?? '[]') as Array<{ name: string; x: string[]; y: number[]; mode: string; fill?: string }>
    expect(traces()).toHaveLength(3)
    expect(traces()[0].y).toHaveLength(60)
    expect(traces()[0].y[0]).toBeCloseTo(-0.59)
    expect(traces()[0].y.at(-1)).toBe(0)
    expect(traces()[1]).toMatchObject({ name: 'TimesFM 路径', y: [0, 0.002, 0.004, 0.006, 0.008, 0.01] })
    expect(traces()[1].x[0]).toBe('2026-09-04')
    expect(traces()[2]).toMatchObject({ name: 'LightGBM 目标', mode: 'markers', x: ['2026-09-11'], y: [0.02] })
    expect(traces().every((trace) => !trace.fill)).toBe(true)
    await user.click(screen.getByRole('radio', { name: '1 日' }))
    expect(traces()[1].y).toEqual([0, 0.002])
    expect(traces()[2].x).toEqual(['2026-09-07'])
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it('keeps quote time distinct and warns when the quote is newer than the frozen origin', async () => {
    route = (path) => path.includes('/latest') ? json(predictionFixture()) : json({
      ticker: '600519.SS', name: '测试股票甲', instrument_type: 'stock', source: 'tencent', currency: 'CNY',
      as_of: '2026-09-07T15:30:00+08:00', last: 106, previous_close: 100, open: 101, high: 108, low: 99,
      change: 6, change_percent: 6, volume: 1000, amount: 100000, bids: [], asks: [], pe_ratio: null,
      pb_ratio: null, market_cap: null, float_market_cap: null, turnover_rate: null, warnings: [],
    })
    renderWorkspace()
    expect(await screen.findByText(/可能已过期/)).toBeVisible()
    expect(screen.getByText('预测基准 2026-09-04')).toBeVisible()
    expect(screen.getByText(/行情时间 2026-09-07/)).toBeVisible()
  })

  it('never normalizes history against a live quote when the origin price is missing', async () => {
    const bundle = predictionFixture()
    bundle.history = [{ date: '2026-09-03', price: 95 }]
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    const chart = await screen.findByRole('img', { name: '累计收益走势' })
    const traces = JSON.parse(chart.dataset.series ?? '[]') as Array<{ name: string }>
    expect(traces.map((trace) => trace.name)).toEqual(['TimesFM 路径', 'LightGBM 目标'])
    expect(screen.getByText(/缺少基准日价格/)).toBeVisible()
  })

  it('does not promote orphaned numerical signals when their model is not ready', async () => {
    const bundle = predictionFixture()
    bundle.status = 'partial'
    bundle.components.lightgbm.status = 'not_ready'
    bundle.components.garch.status = 'unavailable'
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    await screen.findByRole('heading', { name: '测试股票甲' })
    expect(metric('预测累计收益').getByText('+1.00%')).toBeVisible()
    expect(metric('上涨概率').getByText('--')).toBeVisible()
    expect(metric('累计波动').getByText('--')).toBeVisible()
    const chart = screen.getByRole('img', { name: '累计收益走势' })
    expect(chart.dataset.series).not.toContain('LightGBM 目标')
  })

  it('reports a failed job without turning absent numbers into a successful prediction', async () => {
    const user = userEvent.setup()
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: {} }, 404)
      if (init.method === 'POST') return json({ job_id: 'failed-fixture', status: 'pending' }, 202)
      if (path.includes('/jobs/')) return json({ id: 'failed-fixture', status: 'failed', result: null, error: { code: 'insufficient_history', message: 'fixture: insufficient history' } })
      return json({ error: {} }, 503)
    }
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    expect(await screen.findByText(/预测失败：历史样本不足/)).toBeVisible()
    expect(metric('预测累计收益').getByText('--')).toBeVisible()
    expect(screen.getByRole('button', { name: '生成预测' })).toBeEnabled()
  })

  it('cancels a pending job via DELETE and never treats it as a completed result', async () => {
    const user = userEvent.setup()
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: {} }, 404)
      if (init.method === 'POST') return json({ job_id: 'cancel-fixture', status: 'pending' }, 202)
      if (path.includes('/jobs/')) return json({ id: 'cancel-fixture', status: init.method === 'DELETE' ? 'cancelled' : 'pending', cancellation_requested: init.method === 'DELETE', result: null, error: null })
      return json({ error: {} }, 503)
    }
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    await user.click(await screen.findByRole('button', { name: '取消任务' }))
    expect(await screen.findByText('任务已取消')).toBeVisible()
    expect(calls.find((call) => call.method === 'DELETE')?.path).toBe('/api/v2/jobs/cancel-fixture')
    expect(metric('预测累计收益').getByText('--')).toBeVisible()
    expect(screen.getByRole('button', { name: '生成预测' })).toBeEnabled()
  })

  it.each([false, true])('waits for a safe stop when a running job has cancellation_requested=%s', async (alreadyRequested) => {
    const user = userEvent.setup()
    let requested = alreadyRequested
    let stopped = false
    let polls = 0
    let finishDelete!: () => void
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: {} }, 404)
      if (init.method === 'POST') return json({ job_id: 'running-fixture', status: 'running' }, 202)
      if (path === '/api/v2/jobs/running-fixture') {
        if (init.method === 'DELETE') return new Promise((resolve) => {
          finishDelete = () => {
            requested = true
            resolve(json({ id: 'running-fixture', status: 'running', cancellation_requested: true, result: null, error: null }))
          }
        })
        polls += 1
        return json({ id: 'running-fixture', status: stopped ? 'cancelled' : 'running', cancellation_requested: requested, result: null, error: null })
      }
      return json({ error: {} }, 503)
    }
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    const button = await screen.findByRole('button', { name: '取消任务' })
    expect(button).toHaveAttribute('title', '取消任务')
    if (!alreadyRequested) {
      expect(await screen.findByText('正在生成预测…')).toBeVisible()
      await user.click(button)
      expect(button).toBeDisabled()
      expect(screen.queryByText('任务已取消')).not.toBeInTheDocument()
      await act(async () => finishDelete())
    }
    expect(await screen.findByText('取消中，等待当前计算结束')).toBeVisible()
    expect(button).toBeDisabled()
    await user.click(button)
    expect(calls.filter((call) => call.method === 'DELETE')).toHaveLength(alreadyRequested ? 0 : 1)
    expect(screen.getByRole('button', { name: '生成预测' })).toBeDisabled()
    expect(screen.queryByText('任务已取消')).not.toBeInTheDocument()
    expect(metric('上涨概率').getByText('--')).toBeVisible()
    const pollsBefore = polls
    await waitFor(() => expect(polls).toBeGreaterThan(pollsBefore), { timeout: 3000 })
    expect(screen.getByText('取消中，等待当前计算结束')).toBeVisible()
    stopped = true
    expect(await screen.findByText('任务已取消', {}, { timeout: 3000 })).toBeVisible()
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成预测' })).toBeEnabled()
  })

  it('aborts only the client submission when switching stocks and ignores its late response', async () => {
    const user = userEvent.setup()
    const normal = route
    let resolveSubmission!: (response: Response) => void
    route = (path, init) => init.method === 'POST'
      ? new Promise((resolve) => { resolveSubmission = resolve }) : normal(path, init)
    renderWorkspace()
    await screen.findByRole('heading', { name: '测试股票甲' })
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    expect(await screen.findByText('正在提交预测…')).toBeVisible()
    const search = screen.getByRole('combobox', { name: '搜索股票' })
    await user.clear(search)
    await user.type(search, '测试股票乙')
    await screen.findByRole('option', { name: /测试股票乙/ })
    await user.keyboard('{ArrowDown}{Enter}')
    expect(await screen.findByRole('heading', { name: '测试股票乙' })).toBeVisible()
    expect(calls.find((call) => call.method === 'POST')?.signal?.aborted).toBe(true)
    await act(async () => resolveSubmission(json({ job_id: 'old-stock-job', status: 'running' }, 202)))
    expect(calls.some((call) => call.method === 'DELETE' || call.path.includes('old-stock-job'))).toBe(false)
    expect(screen.queryByText('取消中，等待当前计算结束')).not.toBeInTheDocument()
  })

  it('mounts advanced v1 requests only after visiting research experiments', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await screen.findByRole('heading', { name: '测试股票甲' })
    expect(calls.some((call) => call.path.includes('/health'))).toBe(false)
    await user.click(screen.getByRole('link', { name: '历史回测' }))
    expect(await screen.findByRole('button', { name: '开始回测' })).toBeVisible()
    await waitFor(() => expect(calls.some((call) => call.path.includes('/health'))).toBe(true))
  })

  it('translates frozen-vintage and unpinned warnings without leaking unknown diagnostics', async () => {
    const bundle = predictionFixture()
    bundle.warnings = [
      'Frozen current-vintage prices are not PIT corporate-action data; replay does not restore historical adjustments.',
      'TimesFM checkpoint name is recorded; an immutable weight revision is not pinned.',
      'Price adjustment provenance is unknown for this provider.',
      'The available history ends before the latest completed session.',
      `Traceback 内部路径 ${'private-diagnostic '.repeat(1000)}`,
    ]
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    expect(await screen.findByText('已冻结本次获取版本的价格，但并非历史时点的公司行动数据；重放无法还原当时的复权调整。')).toBeVisible()
    expect(screen.getByText('TimesFM 仅记录了权重名称，尚未固定不可变的权重版本。')).toBeVisible()
    expect(screen.getByText('该数据源的价格复权依据尚不明确。')).toBeVisible()
    expect(screen.getByText('可用历史数据尚未覆盖最近一个已结束的交易日。')).toBeVisible()
    expect(screen.getByText('存在未识别的数据或模型警告，请谨慎使用本次预测。')).toBeVisible()
    expect(screen.queryByText(/private-diagnostic|Frozen current-vintage|immutable weight/)).not.toBeInTheDocument()
    expect(metric('上涨概率').getByText('64.0%')).toBeVisible()
  })

  it.each([
    ['artifact_missing', '尚无可用模型制品。'],
    ['stock_models_only', '该模型仅适用于个股，不适用于指数。'],
    ['feature_schema_missing', '模型缺少特征结构信息。'],
    ['trained_through_required', '模型缺少训练截止日期。'],
    ['artifact_after_origin', '模型训练或拟合日期晚于预测基准，不能用于本次预测。'],
    ['trusted_features_required', '缺少可信的历史时点特征。'],
    ['feature_snapshot_invalid', '特征快照无效。'],
    ['dependency_unavailable', '模型运行依赖暂不可用。'],
    ['artifact_or_features_invalid', '模型制品或特征数据校验未通过。'],
    ['artifact_provenance_missing', '模型缺少可追溯的拟合记录。'],
    ['fit_snapshot_unavailable', '模型拟合所用的数据快照暂不可用。'],
    ['insufficient_garch_history', 'GARCH 至少需要 252 个有效日收益样本。'],
    ['artifact_invalid', '模型制品校验未通过。'],
    ['model_load_failed', '模型加载失败，请检查权重下载、依赖和可用内存。'],
    ['5:trusted_features_required;20:artifact_after_origin', '5 日：缺少可信的历史时点特征。；20 日：模型训练或拟合日期晚于预测基准，不能用于本次预测。'],
    ['5:unknown_reason', '5 日：服务暂不可用，请稍后重试。'],
    ['unknown_reason', '服务暂不可用，请稍后重试。'],
    ['constructor', '服务暂不可用，请稍后重试。'],
    [`Traceback 内部路径 ${'private-diagnostic '.repeat(1000)}`, '服务暂不可用，请稍后重试。'],
  ])('displays a bounded Chinese component reason for %s', async (reason, expected) => {
    const bundle = predictionFixture()
    bundle.components.garch = { status: 'unavailable', reason, artifact_id: null }
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    expect(await screen.findByText(expected)).toBeVisible()
    expect(metric('累计波动').getByText('--')).toBeVisible()
    expect(screen.queryByText(reason)).not.toBeInTheDocument()
  })

  it.each(['http', 'job'])('hides huge unknown %s errors even if they contain Chinese text', async (source) => {
    const user = userEvent.setup()
    const error = { code: 'unexpected_failure', message: `Traceback 内部错误 ${'private-diagnostic '.repeat(1000)}` }
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: {} }, 404)
      if (init.method === 'POST') return source === 'http' ? json({ error }, 500) : json({ job_id: 'failed-fixture', status: 'pending' }, 202)
      if (path.includes('/jobs/')) return json({ id: 'failed-fixture', status: 'failed', cancellation_requested: false, result: null, error })
      return json({ error: {} }, 503)
    }
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    expect(await screen.findByText(source === 'http' ? '服务暂不可用，请稍后重试。' : '预测失败：服务暂不可用，请稍后重试。')).toBeVisible()
    expect(screen.queryByText(/private-diagnostic/)).not.toBeInTheDocument()
    expect(metric('上涨概率').getByText('--')).toBeVisible()
  })

  it('reports a failed cancellation in Chinese and permits retry without claiming cancellation', async () => {
    const user = userEvent.setup()
    let attempts = 0
    route = (path, init) => {
      if (path.includes('/latest')) return json({ error: {} }, 404)
      if (init.method === 'POST') return json({ job_id: 'retry-cancel', status: 'running' }, 202)
      if (init.method === 'DELETE' && ++attempts === 1) return json({ error: { code: 'internal_error', message: 'Internal backend failure' } }, 500)
      if (path.includes('/jobs/')) return json({ id: 'retry-cancel', status: 'running', cancellation_requested: attempts > 1, result: null, error: null })
      return json({ error: {} }, 503)
    }
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: '生成预测' }))
    await user.click(await screen.findByRole('button', { name: '取消任务' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂时无法处理请求，请稍后重试。')
    expect(screen.getByRole('button', { name: '取消任务' })).toBeEnabled()
    expect(screen.queryByText('任务已取消')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消任务' }))
    expect(await screen.findByText('取消中，等待当前计算结束')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it.each([[null, '--'], [-0.1, '--'], [1.1, '--'], [0, '0.0%'], [1, '100.0%']] as const)('does not fabricate or clamp probability %s', async (probability, expected) => {
    const bundle = predictionFixture()
    bundle.horizons[1].up_probability = probability
    route = (path) => path.includes('/latest') ? json(bundle) : json({ error: {} }, 503)
    renderWorkspace()
    await screen.findByRole('heading', { name: '测试股票甲' })
    expect(metric('上涨概率').getByText(expected)).toBeVisible()
  })
})