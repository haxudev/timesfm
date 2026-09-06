import { expect, test, type Page } from '@playwright/test'

test.skip(process.env.PLAYWRIGHT_EVIDENCE_LIVE !== '1', 'Requires a seeded, local read-only evidence API')

async function navigate(page: Page, name: string) {
  const link = page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name, exact: true })
  if (!await link.isVisible()) {
    await page.getByRole('combobox', { name: '工作区' }).selectOption(['行情查看', '个股预测'].includes(name) ? 'viewer' : 'research')
  }
  await link.click()
}

test('real evidence uses separate data and model destinations and preserves predictions', async ({ page }, testInfo) => {
  const errors: string[] = []
  const evidenceRequests: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => { if (request.url().endsWith('/api/v2/evidence')) evidenceRequests.push(request.url()) })
  await page.goto('/#prediction')
  await expect(page.getByRole('heading', { name: '牛来', exact: true })).toBeVisible()
  await expect(page.getByText('预测基准 2026-09-04', { exact: true })).toBeVisible()
  await expect(page.getByText('无法连接后端服务，请检查服务是否已启动。', { exact: true })).toHaveCount(0)
  expect(evidenceRequests).toHaveLength(0)
  await page.getByRole('radio', { name: '20 日', exact: true }).check()
  await navigate(page, '数据状态')
  const evidence = page.getByRole('region', { name: '研究评估' })
  await expect(evidence.getByText('35,589', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '个股预测' })).toHaveCount(0)
  await expect(evidence.getByRole('table', { name: '候选与基线对照' })).toHaveCount(0)
  const sources = evidence.getByRole('region', { name: '免费数据源' })
  await expect(sources.getByText('AKShare', { exact: true })).toBeVisible()
  await expect(sources.getByText('Tushare', { exact: true })).toBeVisible()
  await expect(sources.getByText('BaoStock', { exact: true })).toBeVisible()
  await expect(sources.getByText(/最近采集：账号限频/)).toBeVisible()
  await expect(sources.getByText(/历史行业：无权限/)).toBeVisible()
  await expect(evidence.getByText(/历史时点验证未通过/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('navigation-data.png'), fullPage: true })
  const loadedRequests = evidenceRequests.length
  await page.getByRole('link', { name: '模型评估', exact: true }).click()
  await expect(evidence.getByText('研究候选 · 未批准上线')).toBeVisible()
  await expect(evidence.getByText('35,589', { exact: true })).toHaveCount(0)
  const table = evidence.getByRole('table', { name: '候选与基线对照' })
  await expect(table.getByText('4.825', { exact: true })).toBeVisible()
  await expect(table.getByText('4.816', { exact: true })).toBeVisible()
  await evidence.getByRole('radio', { name: '20 个交易日' }).check()
  await expect(table.getByText('9.900', { exact: true })).toBeVisible()
  await expect(table.getByText('9.769', { exact: true })).toBeVisible()
  expect(evidenceRequests).toHaveLength(loadedRequests)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  expect(await evidence.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  if (page.viewportSize()!.width <= 900) {
    const contentBox = (await page.getByRole('main').boundingBox())!
    const navigationBox = (await page.getByRole('navigation', { name: '主导航' }).boundingBox())!
    expect(contentBox.y + contentBox.height).toBeLessThanOrEqual(navigationBox.y)
  }
  await page.screenshot({ path: testInfo.outputPath('navigation-models.png'), fullPage: true })
  await page.goBack()
  await expect(page.getByRole('link', { name: '数据状态' })).toHaveAttribute('aria-current', 'page')
  await page.goForward()
  await expect(evidence.getByRole('radio', { name: '20 个交易日' })).toBeChecked()
  await navigate(page, '个股预测')
  await expect(page.getByRole('radio', { name: '20 日', exact: true })).toBeChecked()
  await expect(page.getByRole('group', { name: '预测累计收益' })).not.toContainText('9.900')
  await expect.poll(() => page.locator('.prediction-chart .scatterlayer path.js-line').evaluateAll((paths) =>
    paths.filter((path) => (path as SVGPathElement).getTotalLength() > 10).length)).toBeGreaterThan(0)
  await expect(page.locator('.workspace-view:not([hidden])')).toHaveCount(1)
  await page.screenshot({ path: testInfo.outputPath('navigation-prediction.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('real evidence refresh has no side effects on prediction tasks', async ({ page }) => {
  const mutations: string[] = []
  page.on('request', (request) => { if (['POST', 'DELETE'].includes(request.method())) mutations.push(request.url()) })
  await page.goto('/')
  await navigate(page, '数据状态')
  await expect(page.getByText('35,589', { exact: true })).toBeVisible()
  const refreshed = page.waitForResponse((response) => response.url().endsWith('/api/v2/evidence') && response.status() === 200)
  await page.getByRole('button', { name: '刷新研究报告' }).click()
  const response = await refreshed
  expect((await response.json()).experiment.training_snapshot_id).toBe('1df82bf2455abb50b3f00ec2fed14c7ef5badf3de514a31325b605e30d0e8f97')
  expect(mutations).toEqual([])
})

test('independent research pages are compact and preserve separate inputs', async ({ page }, testInfo) => {
  const errors: string[] = []
  const mutations: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => { if (['POST', 'DELETE'].includes(request.method())) mutations.push(request.url()) })
  await page.goto('/#research')
  await expect(page.getByRole('link', { name: '预测实验', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { name: '预测实验', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始回测' })).toHaveCount(0)
  await page.getByRole('spinbutton', { name: '上下文长度' }).fill('256')
  await page.getByRole('link', { name: '预测实验', exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: '历史回测' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: '开始回测' })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始预测' })).toHaveCount(0)
  await expect(page.getByRole('spinbutton', { name: '上下文长度' })).toHaveValue('512')
  await page.getByRole('spinbutton', { name: '回测窗口数' }).fill('12')
  await page.getByRole('link', { name: '预测实验', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: '上下文长度' })).toHaveValue('256')
  await page.goBack()
  await expect(page.getByRole('link', { name: '历史回测' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('spinbutton', { name: '回测窗口数' })).toHaveValue('12')
  const navigation = page.getByRole('navigation', { name: '主导航' })
  const size = page.viewportSize()!
  for (const link of await navigation.getByRole('link').all()) {
    if (!await link.isVisible()) continue
    const box = (await link.boundingBox())!
    expect(box.height).toBeGreaterThanOrEqual(44)
    expect(box.width).toBeGreaterThanOrEqual(60)
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(size.width)
  }
  if (size.width <= 900) {
    expect((await navigation.boundingBox())!.y).toBeGreaterThan(size.height - 100)
    const footer = page.locator('.workspace-footer')
    await footer.scrollIntoViewIfNeeded()
    const footerBox = (await footer.boundingBox())!
    expect(footerBox.y + footerBox.height).toBeLessThanOrEqual((await navigation.boundingBox())!.y)
  } else {
    const sidebar = (await page.locator('.workspace-sidebar').boundingBox())!
    expect((await page.getByRole('main').boundingBox())!.x).toBeGreaterThanOrEqual(sidebar.width)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('navigation-research.png'), fullPage: true })
  expect(mutations).toEqual([])
  expect(errors).toEqual([])
})

test('market is the first read-only page and connects stock selection to prediction', async ({ page }, testInfo) => {
  const mutations: string[] = []
  const reads: string[] = []
  page.on('request', (request) => {
    if (['POST', 'DELETE'].includes(request.method())) mutations.push(request.url())
    if (request.url().includes('/api/')) reads.push(new URL(request.url()).pathname)
  })
  await page.goto('/')
  const navigation = page.getByRole('navigation', { name: '主导航' })
  await expect(navigation.getByRole('link', { name: '行情查看' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { name: '行情查看', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '行情快照' })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始预测' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '开始回测' })).toHaveCount(0)
  expect(reads.some((path) => path.endsWith('/health') || path.endsWith('/evidence'))).toBe(false)
  await page.getByRole('radio', { name: '大盘指数', exact: true }).check()
  await page.getByRole('combobox', { name: '指数', exact: true }).selectOption('000300.SS')
  await page.getByRole('link', { name: '查看预测', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '搜索股票' })).toHaveValue('000300.SS')
  await navigate(page, '行情查看')
  await expect(page.getByRole('combobox', { name: '指数', exact: true })).toHaveValue('000300.SS')
  await page.getByRole('radio', { name: '个股', exact: true }).check()
  await expect(page.getByRole('combobox', { name: '搜索股票' })).toHaveValue('600519.SS')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('market-default.png'), fullPage: true })
  expect(mutations).toEqual([])
})