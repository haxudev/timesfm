import { expect, test, type Page, type Route } from '@playwright/test'
import { partialPredictionFixture, predictionFixture } from '../src/test/predictionFixtures'

type BoundaryCall = { path: string; method: string; body: unknown }
type Reply = { body: unknown; status?: number }

async function intercept(page: Page, respond: (call: BoundaryCall) => Reply | Promise<Reply>) {
  const calls: BoundaryCall[] = []
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const call = { path: url.pathname + url.search, method: request.method(), body: request.postDataJSON() }
    calls.push(call)
    const reply = call.method === 'OPTIONS' ? { body: {} } : await respond(call)
    await route.fulfill({
      status: reply.status ?? 200, json: reply.body,
      headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS' },
    })
  })
  return calls
}

const absent = { status: 404, body: { error: { code: 'prediction_not_found', message: 'Fixture: no saved prediction' } } }
const symbols = { body: { items: [{ ticker: '000001.SZ', name: '测试股票乙', instrument_type: 'stock' }] } }

function quoteFixture(ticker = '600519.SS') {
  return { body: {
    ticker, name: ticker === '600519.SS' ? '测试股票甲' : '测试股票乙', instrument_type: 'stock',
    source: 'tencent', currency: 'CNY', as_of: '2026-09-04T15:30:00+08:00', last: 100, previous_close: 95,
    open: 96, high: 101, low: 94, change: 5, change_percent: 5.26, volume: 1000, amount: 100000,
    bids: [], asks: [], pe_ratio: null, pb_ratio: null, market_cap: null, float_market_cap: null, turnover_rate: null, warnings: [],
  } }
}

async function chooseSecondStock(page: Page) {
  const search = page.getByRole('combobox', { name: '搜索股票' })
  await search.fill('测试股票乙')
  await expect(page.getByRole('option', { name: /测试股票乙/ })).toBeVisible()
  await search.press('ArrowDown')
  await search.press('Enter')
  await expect(page.getByRole('heading', { name: '测试股票乙' })).toBeVisible()
}

async function verifyPlotAndLayout(page: Page) {
  const chart = page.locator('.prediction-chart.js-plotly-plot')
  await expect(chart).toHaveCount(1)
  await expect.poll(() => chart.locator('.scatterlayer path.js-line').evaluateAll((paths) =>
    paths.filter((path) => (path as SVGPathElement).getTotalLength() > 10 && getComputedStyle(path).stroke !== 'none').length,
  )).toBeGreaterThan(0)
  const box = await chart.boundingBox()
  expect(box?.width).toBeGreaterThan(250)
  expect(box?.height).toBeGreaterThan(250)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const overflows = await page.locator('.prediction-signals > div, .prediction-actions button, .stock-identity h2').evaluateAll((elements) =>
    elements.filter((element) => {
      const box = element.getBoundingClientRect()
      return box.left < 0 || box.right > innerWidth + 1 || element.scrollWidth > element.clientWidth + 1
    }).map((element) => element.textContent),
  )
  expect(overflows).toEqual([])
}

async function verifyCancelIcon(page: Page) {
  const appearance = await page.getByRole('button', { name: '取消任务' }).locator('svg').evaluate((icon) => ({
    stroke: getComputedStyle(icon).stroke,
    background: getComputedStyle(icon.parentElement!).backgroundColor,
    width: icon.getBoundingClientRect().width,
  }))
  expect(appearance.width).toBeGreaterThanOrEqual(18)
  expect(appearance.stroke).not.toBe(appearance.background)
}

test('stock search, submission, polling completion and local horizons render a real plot', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  let polls = 0
  const bundle = predictionFixture('000001.SZ', '测试股票乙')
  const calls = await intercept(page, (call) => {
    if (call.path.includes('/symbols?')) return symbols
    if (call.path.includes('/quotes/')) return quoteFixture(call.path.includes('000001.SZ') ? '000001.SZ' : '600519.SS')
    if (call.path.endsWith('/latest')) return absent
    if (call.method === 'POST' && call.path === '/api/v2/predictions') return { status: 202, body: { job_id: 'fixture-job', status: 'pending' } }
    if (call.path === '/api/v2/jobs/fixture-job') return { body: ++polls < 3
      ? { id: 'fixture-job', status: polls === 1 ? 'pending' : 'running', cancellation_requested: false, result: null, error: null }
      : { id: 'fixture-job', status: 'succeeded', cancellation_requested: false, result: bundle, error: null } }
    return absent
  })
  await page.goto('/')
  await expect(page.getByText('暂无预测，请生成预测。')).toBeVisible()
  expect(calls.some((call) => call.path === '/api/v2/predictions/600519.SS/latest')).toBe(true)
  expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  await chooseSecondStock(page)
  await page.getByRole('radio', { name: '1 日' }).check()
  await page.getByRole('button', { name: '生成预测' }).click()
  await expect(page.getByText('排队中…', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成预测' })).toBeDisabled()
  await expect(page.getByText('正在生成预测…', { exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: '预测累计收益' })).toContainText('+0.40%')
  expect(calls.filter((call) => call.method === 'POST')).toEqual([{ path: '/api/v2/predictions', method: 'POST', body: { ticker: '000001.SZ', horizon: 1 } }])
  await page.getByRole('radio', { name: '20 日' }).check()
  await expect(page.getByRole('group', { name: '预测累计收益' })).toContainText('+8.00%')
  expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  await verifyPlotAndLayout(page)
  await expect(page.getByRole('button', { name: '开始回测' })).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('prediction.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('switching stock ignores the old in-flight job result', async ({ page }) => {
  let releaseOld!: () => void
  let oldRequested = false
  const gate = new Promise<void>((resolve) => { releaseOld = resolve })
  const second = predictionFixture('000001.SZ', '测试股票乙')
  second.horizons[1].lightgbm_return = 0.07
  const calls = await intercept(page, async (call) => {
    if (call.path.includes('/symbols?')) return symbols
    if (call.path.includes('/quotes/')) return quoteFixture(call.path.includes('000001.SZ') ? '000001.SZ' : '600519.SS')
    if (call.path.includes('000001.SZ/latest')) return { body: second }
    if (call.path.endsWith('/latest')) return absent
    if (call.method === 'POST') return { status: 202, body: { job_id: 'fixture-old-job', status: 'pending' } }
    if (call.path.includes('/jobs/')) {
      oldRequested = true
      await gate
      return { body: { id: 'fixture-old-job', status: 'succeeded', cancellation_requested: false, result: predictionFixture(), error: null } }
    }
    return absent
  })
  await page.goto('/')
  await page.getByRole('button', { name: '生成预测' }).click()
  await expect.poll(() => oldRequested).toBe(true)
  await chooseSecondStock(page)
  releaseOld()
  await expect(page.getByRole('group', { name: '预测累计收益' })).toContainText('+7.00%')
  await expect(page.getByRole('heading', { name: '测试股票甲' })).toHaveCount(0)
  await verifyPlotAndLayout(page)
  expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
})

test('partial models have nullable signals and no invented LightGBM chart', async ({ page }, testInfo) => {
  const bundle = partialPredictionFixture()
  bundle.warnings = [
    'Frozen current-vintage prices are not PIT corporate-action data; replay does not restore historical adjustments.',
    'TimesFM checkpoint name is recorded; an immutable weight revision is not pinned.',
  ]
  await intercept(page, (call) => call.path.endsWith('/latest') ? { body: bundle } : quoteFixture())
  await page.goto('/')
  await expect(page.getByText('部分模型不可用', { exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: '预测累计收益' })).toContainText('TimesFM')
  await expect(page.getByRole('group', { name: '上涨概率' })).toContainText('--')
  await expect(page.getByRole('group', { name: '累计波动' })).toContainText('--')
  await expect(page.getByText('1 日：尚无可用模型制品。；5 日：尚无可用模型制品。；20 日：尚无可用模型制品。')).toBeVisible()
  await expect(page.getByText(/已冻结本次获取版本的价格/)).toBeVisible()
  await expect(page.getByText(/尚未固定不可变的权重版本/)).toBeVisible()
  await expect(page.getByText(/artifact_missing|Frozen current-vintage/)).toHaveCount(0)
  await verifyPlotAndLayout(page)
  await expect(page.locator('.prediction-chart .legend')).not.toContainText('LightGBM')
  await page.screenshot({ path: testInfo.outputPath('partial.png'), fullPage: true })
})

test('no models never manufacture success, a stock name or a probability', async ({ page }) => {
  const bundle = partialPredictionFixture()
  bundle.name = null
  bundle.components.timesfm = { status: 'unavailable', reason: 'Fixture: weights missing', artifact_id: null }
  bundle.path = []
  bundle.horizons.forEach((item) => { item.timesfm_return = null })
  await intercept(page, (call) => call.path.endsWith('/latest') ? { body: bundle } : absent)
  await page.goto('/')
  await expect(page.getByText('暂无可用模型', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '600519.SS' })).toBeVisible()
  await expect(page.getByRole('group', { name: '预测累计收益' })).toContainText('--')
  await expect(page.getByText('50.0%', { exact: false })).toHaveCount(0)
  await verifyPlotAndLayout(page)
})

for (const initialStatus of ['pending', 'running'] as const) {
  test(`cancels a ${initialStatus} job without claiming an instant safe stop`, async ({ page }, testInfo) => {
    let requested = false
    let stopped = false
    let polls = 0
    const calls = await intercept(page, (call) => {
      if (call.path.endsWith('/latest')) return absent
      if (call.path.includes('/quotes/')) return quoteFixture()
      if (call.method === 'POST') return { status: 202, body: { job_id: 'cancel-job', status: initialStatus } }
      if (call.path === '/api/v2/jobs/cancel-job') {
        if (call.method === 'DELETE') {
          requested = true
          stopped = initialStatus === 'pending'
        } else polls += 1
        return { body: { id: 'cancel-job', status: stopped ? 'cancelled' : initialStatus, cancellation_requested: requested, result: null, error: null } }
      }
      return absent
    })
    await page.goto('/')
    await page.getByRole('button', { name: '生成预测' }).click()
    await expect(page.getByText(initialStatus === 'pending' ? '排队中…' : '正在生成预测…', { exact: true })).toBeVisible()
    const cancel = page.getByRole('button', { name: '取消任务' })
    await expect(cancel).toHaveAttribute('title', '取消任务')
    await cancel.hover()
    await verifyCancelIcon(page)
    await cancel.click()
    if (initialStatus === 'running') {
      await expect(page.getByText('取消中，等待当前计算结束', { exact: true })).toBeVisible()
      await expect(cancel).toBeDisabled()
      await verifyCancelIcon(page)
      await expect(page.getByRole('button', { name: '生成预测' })).toBeDisabled()
      await expect(page.getByText('任务已取消', { exact: true })).toHaveCount(0)
      const previousPolls = polls
      await expect.poll(() => polls).toBeGreaterThan(previousPolls)
      await expect(page.getByText('取消中，等待当前计算结束', { exact: true })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      expect(await page.locator('.prediction-status').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath('cancellation-waiting.png'), fullPage: true })
      stopped = true
    }
    await expect(page.getByText('任务已取消', { exact: true })).toBeVisible()
    await expect(cancel).toHaveCount(0)
    await expect(page.getByRole('button', { name: '生成预测' })).toBeEnabled()
    await expect(page.getByRole('group', { name: '上涨概率' })).toContainText('--')
    await expect(page.getByText('50.0%', { exact: true })).toHaveCount(0)
    expect(calls.filter((call) => call.method === 'DELETE')).toEqual([{ path: '/api/v2/jobs/cancel-job', method: 'DELETE', body: null }])
  })
}