export interface EvidenceHorizon {
  horizon: 1 | 5 | 20
  test_rows: number | null
  test_origins: number | null
  mae: number | null
  baseline_mae: number | null
  brier: number | null
  baseline_brier: number | null
  auc: number | null
  mae_beats_baseline: boolean | null
  brier_beats_baseline: boolean | null
}

export interface EvidenceOverview {
  status: 'empty' | 'partial' | 'ready'
  usage: 'local_research_only'
  production_ready: false
  sources?: Array<{
    provider: 'akshare' | 'tushare' | 'baostock'
    collection: {
      snapshot_id: string
      checked_at: string | null
      data_as_of: string | null
      status: 'succeeded' | 'partial' | 'failed' | 'unknown'
      requested: number | null
      succeeded: number | null
      industry_status: 'failed' | 'downloaded_publication_unverified' | 'unknown'
      error_codes: string[]
    } | null
    access: {
      snapshot_id: string
      checked_at: string | null
      checks: Array<{ check: string; status: string; rows: number | null }>
    } | null
  }>
  source_check: {
    snapshot_id: string
    checked_at: string | null
    data_as_of: string | null
    scope: 'sample' | 'unspecified'
    requested: number | null
    succeeded: number | null
    failed: number | null
    universe_total: number | null
    status: 'succeeded' | 'partial' | 'failed' | 'unknown'
    industry_status: 'failed' | 'downloaded_publication_unverified' | 'unknown'
  } | null
  experiment: {
    training_snapshot_id: string
    readiness_snapshot_id: string | null
    dataset_ids: Record<string, string>
    evaluated_at: string | null
    mode: 'strict_pit' | 'historical_research' | 'unknown'
    feature_set: 'price_index_v1' | 'research_features_v1' | 'unknown'
    approval: 'not_approved'
    historical_ready: boolean
    strict_pit_ready: boolean
    history: { start: string | null; end: string | null; sessions: number | null; tickers: number | null }
    quality: { stock_rows: number | null; feature_rows: number | null; missing_stock_sessions: number | null; excluded_feature_rows: number | null }
    timesfm_context_eligible: number | null
    garch_history_eligible: number | null
    limitations: string[]
    horizons: EvidenceHorizon[]
  } | null
}