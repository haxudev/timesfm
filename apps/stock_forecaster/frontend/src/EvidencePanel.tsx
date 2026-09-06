import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Database, FlaskConical, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api } from './api'
import type { EvidenceHorizon } from './evidenceTypes'

const count = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '--' : value.toLocaleString('zh-CN')
const metric = (value: number | null | undefined, percent = false) => value == null || !Number.isFinite(value) ? '--' : (value * (percent ? 100 : 1)).toFixed(percent ? 3 : 4)
const comparison = (value: boolean | null | undefined) => value == null ? '未评估' : value ? '优于基线' : '未优于基线'
const time = (value: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '--'
const limitationNames: Record<string, string> = {
  historical_industry_publication_unverified: '历史行业及公开时间未核验',
  historical_price_vintage_unverified: '历史价格修订版本未核验',
  historical_universe_unverified: '历史股票池及退市覆盖未核验',
  industry_features_not_in_specification: '本实验不含行业特征',
  not_point_in_time_backtest: '非历史时点回测',
  single_chronological_fold_not_validated: '仅单次时间切分，未完成多窗口验证',
  daily_variance_proxy_only: '风险验证仅使用日线方差代理',
}

const providerNames = { akshare: 'AKShare', tushare: 'Tushare', baostock: 'BaoStock' }
const checkNames: Record<string, string> = { daily: '日线', adj_factor: '复权因子', index_daily: '指数日线', trade_cal: '交易日历',
  stock_basic_listed: '上市证券', stock_basic_delisted: '退市证券', industry_current: '当前行业', industry_history: '历史行业' }
const accessNames: Record<string, string> = { available: '可用', empty: '空数据', permission_denied: '无权限', authentication_failed: '认证失败',
  rate_limited: '账号限频', not_configured: '未配置', unavailable: '暂不可用' }

export function EvidenceSection() {
  const [open, setOpen] = useState(false)
  return <details className="research-details evidence-details" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><ChevronDown size={18} aria-hidden="true" />数据与模型评估</summary>
    {open && <EvidencePanel />}
  </details>
}

export function EvidencePanel({ view = 'all', active = true }: { view?: 'all' | 'data' | 'models'; active?: boolean }) {
  const showData = view !== 'models'
  const showModels = view !== 'data'
  const [horizon, setHorizon] = useState<EvidenceHorizon['horizon']>(5)
  const query = useQuery({ queryKey: ['research-evidence'], queryFn: ({ signal }) => api.evidence(signal),
    enabled: active, retry: false, refetchOnWindowFocus: false, staleTime: 60_000 })
  const overview = query.data
  const experiment = overview?.experiment
  const source = overview?.source_check
  const selected = experiment?.horizons.find((item) => item.horizon === horizon)
  return <section className="evidence-panel" aria-label="研究评估">
    <div className="evidence-heading">
      <div>{view === 'data' ? <Database size={18} aria-hidden="true" /> : <FlaskConical size={18} aria-hidden="true" />}<h2>{view === 'data' ? '数据状态' : view === 'models' ? '模型评估' : '研究候选 · 未批准上线'}</h2></div>
      <button type="button" className="icon-button" title="刷新研究报告" aria-label="刷新研究报告" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={17} aria-hidden="true" /></button>
    </div>
    {view === 'models' && <p className="evidence-boundary evidence-approval">研究候选 · 未批准上线</p>}
    <p className="evidence-boundary">仅限本机研究。实验指标不是当前个股预测；模型与数据尚未获准用于生产。</p>
    {query.isPending && <p role="status">正在读取研究报告…</p>}
    {query.isError && <p role="alert" className="error">研究报告暂不可用{overview ? '，以下为上次读取结果' : ''}。</p>}
    {overview?.status === 'empty' && <p role="status">暂无已留存的研究报告</p>}
    {showData && overview?.sources?.length ? <section className="source-status-list" aria-label="免费数据源">
      <h3>免费数据源</h3>
      <p className="data-meta">各来源独立留存，仅使用现有权限。接口可用不代表历史时点验证通过，不自动跨源拼接。</p>
      {overview.sources.map((item) => <div className="source-status-row" key={item.provider}>
        <strong>{providerNames[item.provider]}</strong><div>
          {item.collection ? <>
            <span>{item.collection.error_codes.includes('tushare_rate_limited') ? '最近采集：账号限频，未自动重试' : `最近采集：${item.collection.status === 'succeeded' ? '完成' : item.collection.status === 'partial' ? '部分完成' : '失败或未知'}`} · {count(item.collection.succeeded)} / {count(item.collection.requested)}</span>
            <p className="data-meta">{time(item.collection.checked_at)} · 行情截至 {item.collection.data_as_of ?? '--'} · {item.collection.industry_status === 'downloaded_publication_unverified' ? '行业已获取，历史公开时间未核验' : item.collection.industry_status === 'failed' ? '行业获取失败' : '行业尚未核验'}</p>
          </> : <span>尚无完整采集记录</span>}
          {item.access && <><p className="data-meta">接口探针 {time(item.access.checked_at)}</p>
            <div className="source-access-checks">{item.access.checks.map((check) => <span key={check.check}>{checkNames[check.check] ?? '接口'}：{accessNames[check.status] ?? '未知'}{check.status === 'available' ? ` (${count(check.rows)} 条)` : ''}</span>)}</div></>}
        </div>
      </div>)}
    </section> : null}
    {showData && overview && !source && overview.status !== 'empty' && <p className="evidence-boundary">尚无来源探针记录</p>}
    {showData && source && !overview?.sources?.length && <section className="evidence-source" aria-label="最近来源探针">
      <h3>最近来源探针 <span>{source.status === 'succeeded' ? '采集完成' : source.status === 'partial' ? '部分完成' : source.status === 'failed' ? '采集失败' : '状态未知'}</span></h3>
      <div className="evidence-source-lines"><span>行情成功 {count(source.succeeded)} / {count(source.requested)}</span>
        <span>{source.industry_status === 'failed' ? '行业接口本次失败' : source.industry_status === 'downloaded_publication_unverified' ? '行业已下载，公开时间未核验' : '行业状态未知'}</span>
        <span>{source.scope === 'sample' ? '小样本探针，非全市场覆盖' : '覆盖范围未核验'}</span></div>
      <p className="data-meta">检查时间 {time(source.checked_at)} · 行情截至 {source.data_as_of ?? '--'}。此探针与下方历史训练集分别留存。</p>
    </section>}
    {experiment && <>
      <div className="evidence-experiment-heading"><h3>历史实验 · {experiment.feature_set === 'price_index_v1' ? '量价与指数' : experiment.feature_set === 'research_features_v1' ? '量价与行业' : '特征口径未知'}</h3>
        <span>评估 {time(experiment.evaluated_at)}</span></div>
      {!experiment.readiness_snapshot_id ? <p className="evidence-boundary">缺少与该训练集匹配的就绪报告</p>
        : <p className="evidence-boundary">{experiment.historical_ready ? '满足历史实验训练门槛' : '历史实验训练门槛未通过'} · {experiment.strict_pit_ready ? '历史时点数据门槛通过，预测有效性仍未批准' : '历史时点验证未通过'}</p>}
      <p className="data-meta">{experiment.history.start ?? '--'} 至 {experiment.history.end ?? '--'} · {count(experiment.history.sessions)} 个交易日 · {count(experiment.history.tickers)} 只样本股票</p>
      {showData && <dl className="evidence-counts">
        <div><dt>股票日线</dt><dd>{count(experiment.quality.stock_rows)}</dd></div>
        <div><dt>有效特征样本</dt><dd>{count(experiment.quality.feature_rows)}</dd></div>
        <div><dt>缺失交易日记录</dt><dd>{count(experiment.quality.missing_stock_sessions)}</dd></div>
        <div><dt>排除窗口样本</dt><dd>{count(experiment.quality.excluded_feature_rows)}</dd></div>
      </dl>}
      {showModels && <><div className="evidence-horizon-toolbar"><h3>留出段结果</h3><div className="segments" role="radiogroup" aria-label="实验期限">
        {([1, 5, 20] as const).map((days) => <label key={days}><input type="radio" name="evidence-horizon" checked={horizon === days} onChange={() => setHorizon(days)} aria-label={`${days} 个交易日`} />{days} 日</label>)}
      </div></div>
      <p className="data-meta">{count(selected?.test_rows)} 条测试记录 · {count(selected?.test_origins)} 个不同预测日期。跨股票与重叠期限样本并非独立。</p>
      <div className="table-wrap evidence-table"><table aria-label="候选与基线对照">
        <thead><tr><th scope="col">指标</th><th scope="col">候选</th><th scope="col">基线</th><th scope="col">对照</th></tr></thead>
        <tbody>
          <tr><th scope="row">收益 MAE<small>百分点 · 越低越好</small></th><td>{metric(selected?.mae, true)}</td><td>{metric(selected?.baseline_mae, true)}</td><td className={selected?.mae_beats_baseline === true ? 'evidence-better' : 'evidence-neutral'}>{comparison(selected?.mae_beats_baseline)}</td></tr>
          <tr><th scope="row">概率 Brier<small>越低越好</small></th><td>{metric(selected?.brier)}</td><td>{metric(selected?.baseline_brier)}</td><td className={selected?.brier_beats_baseline === true ? 'evidence-better' : 'evidence-neutral'}>{comparison(selected?.brier_beats_baseline)}</td></tr>
          <tr><th scope="row">概率 AUC</th><td>{metric(selected?.auc)}</td><td>--</td><td>未检验显著性</td></tr>
        </tbody>
      </table></div>
      <p className="data-meta">收益基线为零收益，概率基线为训练期上涨频率。数值优于基线不代表统计显著或交易获利。</p></>}
      {showData && <><div className="evidence-models"><span>TimesFM 输入长度达标：{count(experiment.timesfm_context_eligible)} 只</span><span>GARCH 历史长度达标：{count(experiment.garch_history_eligible)} 只</span></div>
      <p className="data-meta">长度达标不等于已运行、拟合收敛或预测有效。上述候选评估不代表当前选择的个股。</p></>}
      <details className="research-details"><summary><ChevronDown size={16} aria-hidden="true" />限制与数据快照</summary>
        {experiment.limitations.length > 0 && <ul className="evidence-limitations">{[...new Set(experiment.limitations.map((code) => limitationNames[code] ?? '另有数据或验证限制'))].map((text) => <li key={text}>{text}</li>)}</ul>}
        <dl className="provenance"><dt>训练报告</dt><dd>{experiment.training_snapshot_id}</dd><dt>匹配的就绪报告</dt><dd>{experiment.readiness_snapshot_id ?? '--'}</dd>
          {Object.entries(experiment.dataset_ids).map(([name, identifier]) => <div key={name}><dt>{name === 'bars' ? '股票日线' : name === 'index' ? '指数日线' : '行业'}快照</dt><dd>{identifier}</dd></div>)}
        </dl>
      </details>
    </>}
    {overview && !experiment && overview.status !== 'empty' && <p>尚无已留存的候选训练评估。</p>}
  </section>
}