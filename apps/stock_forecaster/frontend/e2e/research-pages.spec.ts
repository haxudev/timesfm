import { expect, test, type Page } from '@playwright/test'
import { backtestFixture, forecastFixture } from '../src/test/fixtures'

async function mockResearch(page: Page) {
  const calls: Array<{ path: string; method: string; body: unknown }> = []
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    calls.push({ path, method: request.method(), body: request.postDataJSON() })
    const body = path.endsWith('/health') ? { status: 'ok', device: 'cpu', model_state: 'ready' }
      : path.endsWith('/forecasts') ? forecastFixture : path.endsWith('/backtests') ? { ...backtestFixture, ticker: request.postDataJSON().ticker } : { error: { code: 'not_found' } }
    await route.fulfill({ json: body })
  })
  return calls
}

test('independent forecast results switch one chart at a time and export the frozen response', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const calls = await mockResearch(page)
  await page.goto('/#forecast')
  await page.getByLabel('股票代码').fill('SPY')
  await page.getByRole('radio', { name: '1 日', exact: true }).check()
  await page.getByRole('button', { name: '开始预测', exact: true }).click()
  await expect(page.getByRole('region', { name: '预测结果' })).toBeVisible()
  await expect(page.getByLabel('结果参数')).toContainText('SPY')
  const chart = page.locator('.research-output .js-plotly-plot:visible')
  await expect(chart).toHaveCount(1)
  await expect.poll(() => chart.locator('.scatterlayer path.point, .scatterlayer path.js-line').count()).toBeGreaterThan(0)
  await page.getByRole('radio', { name: '收益分布', exact: true }).check()
  await expect(chart).toHaveCount(1)
  await expect.poll(() => chart.locator('.scatterlayer path.point').count()).toBeGreaterThan(0)
  await page.getByRole('radio', { name: '逐日明细', exact: true }).check()
  await expect(page.getByRole('table', { name: /逐日预测/ })).toBeVisible()
  await expect(chart).toHaveCount(0)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出预测表格', exact: true }).click()
  expect((await download).suggestedFilename()).toMatch(/\.csv$/)
  await page.getByRole('link', { name: '历史回测', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: '预测天数' })).toHaveValue('5')
  await page.getByRole('link', { name: '预测实验', exact: true }).click()
  await expect(page.getByRole('radio', { name: '逐日明细' })).toBeChecked()
  await page.getByRole('textbox', { name: '股票代码' }).fill('000001')
  await expect(page.getByText('参数已变更，当前结果仍对应上次提交。')).toBeVisible()
  await expect(page.getByLabel('结果参数')).toContainText('SPY')
  await page.getByRole('radio', { name: '价格路径', exact: true }).check()
  await expect(chart).toHaveCount(1)
  await expect.poll(() => chart.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(250)
  expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('forecast-independent.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('backtest submits its own window settings and retains comparison results', async ({ page }, testInfo) => {
  const calls = await mockResearch(page)
  await page.goto('/#backtest')
  await page.getByRole('radio', { name: '大盘指数', exact: true }).check()
  await page.getByRole('combobox', { name: '指数', exact: true }).selectOption('000300.SS')
  await page.getByRole('spinbutton', { name: '回测窗口数' }).fill('4')
  await page.getByRole('spinbutton', { name: '滑动步长（交易日）' }).fill('2')
  await page.getByRole('button', { name: '开始回测', exact: true }).click()
  const result = page.getByRole('region', { name: '回测结果' })
  await expect(result.getByText('零收益基线', { exact: true })).toBeVisible()
  await expect(page.getByLabel('结果参数')).toContainText('4 个窗口 · 步长 2')
  expect(calls.find((call) => call.path.endsWith('/backtests'))?.body).toMatchObject({ ticker: '000300.SS', evaluation_windows: 4, step_size: 2 })
  await page.getByRole('link', { name: '预测实验', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: '预测天数' })).toHaveValue('5')
  await page.goBack()
  await expect(result.getByText('历史均值基线', { exact: true })).toBeVisible()
  expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('backtest-independent.png'), fullPage: true })
})