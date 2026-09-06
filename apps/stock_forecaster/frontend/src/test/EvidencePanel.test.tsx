import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { EvidencePanel, EvidenceSection } from '../EvidencePanel'

const fixture = {
  status: 'ready', usage: 'local_research_only', production_ready: false,
  source_check: {
    snapshot_id: 'a'.repeat(64), checked_at: '2026-09-06T02:00:00+00:00', data_as_of: '2026-09-04',
    scope: 'sample', requested: 4, succeeded: 4, failed: 0, universe_total: 5556,
    status: 'partial', industry_status: 'failed',
  },
  experiment: {
    training_snapshot_id: 'b'.repeat(64), readiness_snapshot_id: 'c'.repeat(64),
    dataset_ids: { bars: 'd'.repeat(64), index: 'e'.repeat(64) }, evaluated_at: '2026-09-05T13:21:06+00:00',
    mode: 'historical_research', feature_set: 'price_index_v1', approval: 'not_approved',
    historical_ready: true, strict_pit_ready: false,
    history: { start: '2021-09-01', end: '2026-09-04', sessions: 1214, tickers: 30 },
    quality: { stock_rows: 36374, feature_rows: 35589, missing_stock_sessions: 37, excluded_feature_rows: 230 },
    timesfm_context_eligible: 29, garch_history_eligible: 27,
    limitations: ['historical_universe_unverified', 'not_point_in_time_backtest'],
    horizons: [
      { horizon: 1, test_rows: 5380, test_origins: 182, mae: .02112618, baseline_mae: .02101135, brier: .24819163, baseline_brier: .24874028, auc: .55391278, mae_beats_baseline: false, brier_beats_baseline: true },
      { horizon: 5, test_rows: 5248, test_origins: 178, mae: .04825484, baseline_mae: .04815591, brier: .25090775, baseline_brier: .24767049, auc: .51201639, mae_beats_baseline: false, brier_beats_baseline: false },
      { horizon: 20, test_rows: 4754, test_origins: 163, mae: null, baseline_mae: null, brier: null, baseline_brier: null, auc: null, mae_beats_baseline: null, brier_beats_baseline: null },
    ],
  },
}

const clients: QueryClient[] = []
function mount(collapsed = false, view: 'all' | 'data' | 'models' = 'all') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  render(<QueryClientProvider client={client}>{collapsed ? <EvidenceSection /> : <EvidencePanel view={view} />}</QueryClientProvider>)
}

afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); vi.unstubAllGlobals() })

it('fetches evidence only after the section is expanded', async () => {
  const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify(fixture))))
  vi.stubGlobal('fetch', fetcher)
  mount(true)
  expect(fetcher).not.toHaveBeenCalled()
  await userEvent.setup().click(screen.getByText('数据与模型评估'))
  expect(await screen.findByText('35,589')).toBeVisible()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('shows actual experiment metrics, distinct source checks, and never approves deployment', async () => {
  const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify(fixture))))
  vi.stubGlobal('fetch', fetcher)
  mount()
  expect(await screen.findByText('研究候选 · 未批准上线')).toBeVisible()
  expect(await screen.findByText('35,589')).toBeVisible()
  expect(screen.getByText(/历史时点验证未通过/)).toBeVisible()
  expect(screen.getByText('行业接口本次失败')).toBeVisible()
  const table = within(screen.getByRole('table', { name: '候选与基线对照' }))
  expect(table.getByText('4.825')).toBeVisible()
  expect(table.getByText('4.816')).toBeVisible()
  expect(table.getAllByText('未优于基线')).toHaveLength(2)
  expect(fetcher.mock.calls).toHaveLength(1)
  const user = userEvent.setup()
  await user.click(screen.getByRole('radio', { name: '1 个交易日' }))
  expect(table.getByText('2.113')).toBeVisible()
  expect(table.getByText('优于基线')).toBeVisible()
  await user.click(screen.getByRole('radio', { name: '20 个交易日' }))
  expect(table.getAllByText('--').length).toBeGreaterThan(3)
  expect(table.queryByText('0.000')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /上线|启用|训练/ })).not.toBeInTheDocument()
})

it('has an explicit empty state', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({
    status: 'empty', usage: 'local_research_only', production_ready: false, source_check: null, experiment: null,
  })))))
  mount()
  expect(await screen.findByText('暂无已留存的研究报告')).toBeVisible()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('retries failed evidence requests without exposing server details', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { message: 'private-path token=secret' } }), { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(fixture)))
  vi.stubGlobal('fetch', fetcher)
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('研究报告暂不可用')
  expect(screen.queryByText(/private-path/)).not.toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: '刷新研究报告' }))
  expect(await screen.findByText('35,589')).toBeVisible()
})

it('does not claim a missing matched readiness report passed or failed', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({
    ...fixture, status: 'partial', experiment: { ...fixture.experiment, readiness_snapshot_id: null },
  })))))
  mount()
  expect(await screen.findByText('缺少与该训练集匹配的就绪报告')).toBeVisible()
  expect(screen.queryByText('历史时点验证未通过')).not.toBeInTheDocument()
})

it('discloses an absent source probe separately from available training metrics', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...fixture, source_check: null, status: 'partial' })))))
  mount()
  expect(await screen.findByText('尚无来源探针记录')).toBeVisible()
  expect(screen.getByText('35,589')).toBeVisible()
})

it('separates coverage from candidate metrics in the data view', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(fixture)))))
  mount(false, 'data')
  expect(await screen.findByText('35,589')).toBeVisible()
  expect(screen.getByText('行业接口本次失败')).toBeVisible()
  expect(screen.queryByRole('table', { name: '候选与基线对照' })).not.toBeInTheDocument()
  expect(screen.queryByRole('radiogroup', { name: '实验期限' })).not.toBeInTheDocument()
})

it('shows the candidate comparison without repeating source and coverage panels', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(fixture)))))
  mount(false, 'models')
  expect(await screen.findByRole('table', { name: '候选与基线对照' })).toBeVisible()
  expect(screen.getByText('研究候选 · 未批准上线')).toBeVisible()
  expect(screen.queryByRole('region', { name: '最近来源探针' })).not.toBeInTheDocument()
  expect(screen.queryByText('35,589')).not.toBeInTheDocument()
})

it('shows free provider access separately from a later collection rate limit', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...fixture, sources: [
    { provider: 'tushare', collection: { status: 'failed', succeeded: 0, requested: 0, error_codes: ['tushare_rate_limited'], industry_status: 'unknown', checked_at: '2026-09-06T02:00:00+00:00' },
      access: { checked_at: '2026-09-06T01:00:00+00:00', checks: [{ check: 'daily', status: 'available', rows: 6 }, { check: 'industry_history', status: 'permission_denied', rows: 0 }] } },
    { provider: 'baostock', collection: { status: 'succeeded', succeeded: 4, requested: 4, error_codes: [], industry_status: 'downloaded_publication_unverified', checked_at: '2026-09-06T02:00:00+00:00' }, access: null },
  ] })))))
  mount(false, 'data')
  const section = await screen.findByRole('region', { name: '免费数据源' })
  expect(within(section).getByText('Tushare')).toBeVisible()
  expect(within(section).getByText('BaoStock')).toBeVisible()
  expect(within(section).getByText(/账号限频/)).toBeVisible()
  expect(within(section).getByText(/日线：可用/)).toBeVisible()
  expect(within(section).getByText(/历史行业：无权限/)).toBeVisible()
  expect(screen.queryByText('token=secret')).not.toBeInTheDocument()
})