export const fixtureDates = [
  '2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11',
  '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18',
  '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25',
  '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08', '2026-10-09',
]

export function predictionFixture(ticker = '600519.SS', name: string | null = '测试股票甲') {
  return {
    schema_version: 2 as const,
    bundle_id: `fixture-bundle-${ticker}`,
    ticker, name, instrument_type: 'stock' as const,
    origin: '2026-09-04', issued_at: '2026-09-05T08:00:00+08:00',
    snapshot_id: 'fixture-snapshot', status: 'succeeded' as 'succeeded' | 'partial',
    horizons: ([1, 5, 20] as const).map((horizon) => ({
      horizon, target_date: fixtureDates[horizon - 1],
      timesfm_return: horizon * 0.002 as number | null,
      lightgbm_return: horizon * 0.004 as number | null,
      up_probability: 0.64 as number | null,
      volatility: horizon * 0.006 as number | null,
    })),
    path: fixtureDates.map((date, index) => ({ date, cumulative_return: (index + 1) * 0.002 })),
    history: [{ date: '2026-09-02', price: 90 }, { date: '2026-09-03', price: 95 }, { date: '2026-09-04', price: 100 }],
    components: {
      timesfm: { status: 'ready' as 'ready' | 'not_ready' | 'unavailable' | 'not_supported', reason: null as string | null, artifact_id: 'fixture-timesfm' as string | null },
      lightgbm: { status: 'ready' as 'ready' | 'not_ready' | 'unavailable' | 'not_supported', reason: null as string | null, artifact_id: 'fixture-lightgbm' as string | null },
      garch: { status: 'ready' as 'ready' | 'not_ready' | 'unavailable' | 'not_supported', reason: null as string | null, artifact_id: 'fixture-garch' as string | null },
    },
    warnings: [] as string[],
  }
}

export function partialPredictionFixture() {
  const bundle = predictionFixture()
  bundle.status = 'partial'
  bundle.components.lightgbm = { status: 'not_ready', reason: '1:artifact_missing;5:artifact_missing;20:artifact_missing', artifact_id: null }
  bundle.components.garch = { status: 'unavailable', reason: 'insufficient_garch_history', artifact_id: null }
  bundle.horizons.forEach((item) => {
    item.lightgbm_return = null
    item.up_probability = null
    item.volatility = null
  })
  return bundle
}