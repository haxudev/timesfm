import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BarChart3, ChevronDown, Database, FlaskConical, History, LineChart, CandlestickChart, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { EvidencePanel } from './EvidencePanel'
import { MarketPage } from './MarketPage'
import { ForecastExperimentPage, HistoricalBacktestPage } from './ResearchPages'
import { ModelComparison, PredictionSignals } from './PredictionSignals'
import { PredictionChart } from './PredictionChart'
import { predictionDisplay, predictionErrorText, signals } from './prediction'
import { SymbolSearch } from './SymbolSearch'
import type { PredictionHorizon, ResearchSymbol } from './types'

const defaultStock: ResearchSymbol = { ticker: '600519.SS', name: null, instrument_type: 'stock' }

const destinations = [
  { id: 'market', label: '行情查看', icon: CandlestickChart, audience: 'viewer' },
  { id: 'prediction', label: '个股预测', icon: LineChart, audience: 'viewer' },
  { id: 'forecast', label: '预测实验', icon: FlaskConical, audience: 'research' },
  { id: 'backtest', label: '历史回测', icon: History, audience: 'research' },
  { id: 'models', label: '模型评估', icon: BarChart3, audience: 'research' },
  { id: 'data', label: '数据状态', icon: Database, audience: 'research' },
] as const
type WorkspaceView = typeof destinations[number]['id']
const hashView = (): WorkspaceView => window.location.hash === '#research' ? 'forecast' : destinations.find((item) => `#${item.id}` === window.location.hash)?.id ?? 'market'

export function PredictionWorkspace() {
  const [selected, setSelected] = useState(defaultStock)
  const [view, setView] = useState<WorkspaceView>(hashView)
  const [visited, setVisited] = useState<WorkspaceView[]>(() => [hashView()])
  const main = useRef<HTMLElement>(null)
  useEffect(() => {
    const normalizeAlias = () => {
      if (window.location.hash === '#research') window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}#forecast`)
    }
    normalizeAlias()
    const navigate = () => {
      normalizeAlias()
      const next = hashView()
      setView(next)
      setVisited((previous) => previous.includes(next) ? previous : [...previous, next])
      main.current?.focus({ preventScroll: true })
      if (main.current) main.current.scrollTop = 0
      main.current?.scrollIntoView?.({ block: 'start' })
    }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  useEffect(() => { window.dispatchEvent(new Event('resize')) }, [view])
  const title = destinations.find((item) => item.id === view)!.label
  const audience = destinations.find((item) => item.id === view)!.audience
  return <div className="workspace-shell">
    <a className="skip-link" href="#workspace-main" onClick={(event) => {
      event.preventDefault()
      main.current?.focus({ preventScroll: true })
      if (main.current) main.current.scrollTop = 0
      main.current?.scrollIntoView?.({ block: 'start' })
    }}>跳至主内容</a>
    <aside className="workspace-sidebar">
      <div className="sidebar-heading"><span className="sidebar-mark" aria-hidden="true">N</span><span>牛来工作台</span></div>
      <label className="workspace-audience-switch">工作区<select value={audience} onChange={(event) => { window.location.hash = event.target.value === 'viewer' ? 'market' : 'forecast' }}>
        <option value="viewer">用户查看</option><option value="research">研究管理</option>
      </select></label>
      <nav className="workspace-navigation" aria-label="主导航">
        {(['viewer', 'research'] as const).map((group) => <div key={group} className={`navigation-group${audience === group ? ' is-active-group' : ''}`}>
          <p className="navigation-group-title">{group === 'viewer' ? '用户查看' : '研究管理'}</p>
          {destinations.filter((item) => item.audience === group).map(({ id, label, icon: Icon }) => <a key={id} href={`#${id}`} aria-current={view === id ? 'page' : undefined}>
          <Icon size={20} aria-hidden="true" /><span>{label}</span>
        </a>)}</div>)}
      </nav>
      <div className="sidebar-context"><span>当前个股</span><strong>{selected.name ?? selected.ticker}</strong><small>本机研究 · 非生产</small></div>
    </aside>
    <main className="prediction-workspace" id="workspace-main" ref={main} tabIndex={-1}>
    <header className={`workspace-header${audience === 'viewer' ? '' : ' compact'}`}>
      <div className="brand"><h1>牛来</h1><span>大A · {title}</span></div>
      {audience === 'viewer' && <SymbolSearch key={selected.ticker} selected={selected} onSelect={setSelected} />}
    </header>
    <div className="workspace-view" hidden={view !== 'market'}>
      {visited.includes('market') && <MarketPage selected={selected} onSelect={setSelected} active={view === 'market'} />}
    </div>
    <div className="workspace-view" hidden={view !== 'prediction'}>
      {visited.includes('prediction') && <PredictionSession key={selected.ticker} symbol={selected} active={view === 'prediction'} />}
    </div>
    <div className="workspace-view" hidden={view !== 'data'}>
      {visited.includes('data') && <EvidencePanel view="data" active={view === 'data'} />}
    </div>
    <div className="workspace-view" hidden={view !== 'models'}>
      {visited.includes('models') && <EvidencePanel view="models" active={view === 'models'} />}
    </div>
    <div className="workspace-view" hidden={view !== 'forecast'}>
      {visited.includes('forecast') && <ForecastExperimentPage active={view === 'forecast'} />}
    </div>
    <div className="workspace-view" hidden={view !== 'backtest'}>
      {visited.includes('backtest') && <HistoricalBacktestPage active={view === 'backtest'} />}
    </div>
    <footer className="workspace-footer">不构成投资建议。预测存在不确定性，不保证盈利。</footer>
  </main>
  </div>
}

function PredictionSession({ symbol, active }: { symbol: ResearchSymbol; active: boolean }) {
  const { ticker } = symbol
  const [horizon, setHorizon] = useState<PredictionHorizon>(5)
  const client = useQueryClient()
  const controller = useRef<AbortController | null>(null)
  const latestKey = ['prediction', ticker, 'latest']
  const latest = useQuery({ queryKey: latestKey, queryFn: ({ signal }) => api.latestPrediction(ticker, signal), enabled: active, retry: false, refetchOnWindowFocus: false })
  const quote = useQuery({ queryKey: ['compact-quote', ticker], queryFn: ({ signal }) => api.quote(ticker, 'auto', signal), enabled: active, retry: false, refetchOnWindowFocus: false })
  const submit = useMutation({ mutationFn: (selectedHorizon: PredictionHorizon) => {
    controller.current?.abort()
    controller.current = new AbortController()
    return api.createPrediction({ ticker, horizon: selectedHorizon }, controller.current.signal)
  } })
  useEffect(() => () => controller.current?.abort(), [])
  const jobId = submit.data?.job_id
  const jobKey = ['prediction-job', ticker, jobId]
  const job = useQuery({
    queryKey: jobKey,
    queryFn: ({ signal }) => api.predictionJob(jobId!, signal),
    enabled: Boolean(jobId), retry: false, refetchOnWindowFocus: false,
    refetchInterval: (query) => query.state.data && ['pending', 'running'].includes(query.state.data.status) ? 1000 : false,
  })
  const cancel = useMutation({
    onMutate: () => client.cancelQueries({ queryKey: jobKey }),
    mutationFn: () => api.cancelPrediction(jobId!),
    onSuccess: (response) => client.setQueryData(jobKey, response),
  })
  const completed = job.data?.result
  const rawBundle = completed?.ticker === ticker ? completed : latest.data?.ticker === ticker ? latest.data : undefined
  const bundle = rawBundle && predictionDisplay(rawBundle)
  const prediction = bundle?.horizons.find((item) => item.horizon === horizon)
  const currentSignals = signals(bundle, prediction)
  const noModels = bundle && currentSignals.value === null && currentSignals.probability === null && currentSignals.volatility === null
  const busy = submit.isPending || Boolean(jobId && !job.isError && (!job.data || ['pending', 'running'].includes(job.data.status)))
  const noPrediction = latest.error instanceof ApiError && latest.error.status === 404
  const validQuote = quote.data?.ticker === ticker ? quote.data : undefined
  const quoteDate = validQuote && Number.isFinite(Date.parse(validQuote.as_of))
    ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(validQuote.as_of)) : null
  const possiblyStale = bundle && quoteDate && quoteDate > bundle.origin
  const name = bundle?.name ?? validQuote?.name ?? symbol.name
  const error = submit.error ?? job.error ?? cancel.error ?? (!noPrediction ? latest.error : null)
  const generate = () => { cancel.reset(); submit.mutate(horizon) }

  return <section className="prediction-session" aria-label="个股预测">
    <div className="prediction-toolbar">
      <div className="stock-identity"><h2>{name || ticker}</h2>{name && <span>{ticker}</span>}</div>
      <div className="prediction-actions">
        <div className="segments" role="radiogroup" aria-label="预测期限">
          {([1, 5, 20] as const).map((days) => <label key={days}><input type="radio" name="prediction-horizon" checked={horizon === days} onChange={() => setHorizon(days)} />{days} 日</label>)}
        </div>
        <button className="generate-button" type="button" disabled={busy} onClick={generate}><ArrowRight size={18} aria-hidden="true" />生成预测</button>
        {job.data && ['pending', 'running'].includes(job.data.status) && <button className="icon-button" title="取消任务" aria-label="取消任务" disabled={cancel.isPending || job.data.cancellation_requested} onClick={() => cancel.mutate()}><X size={18} /></button>}
      </div>
    </div>
    {validQuote && <p className="compact-quote">行情 {validQuote.last?.toFixed(2) ?? '--'} {validQuote.instrument_type === 'index' ? '点' : '元'}<span>行情时间 {validQuote.as_of}</span></p>}
    <div className="prediction-meta">
      <span>预测基准 {bundle?.origin ?? '--'}</span>
      <span>{horizon} 个交易日{prediction && ` · 目标 ${prediction.target_date}`}</span>
      {bundle && <span>签发 {bundle.issued_at}</span>}
    </div>
    <div className="prediction-status" role="status" aria-live="polite">
      {submit.isPending ? '正在提交预测…' : busy ? (job.data?.cancellation_requested ? '取消中，等待当前计算结束' : job.data?.status === 'running' ? '正在生成预测…' : '排队中…')
        : job.data?.status === 'failed' ? `预测失败：${predictionErrorText(job.data.error)}`
          : job.data?.status === 'cancelled' ? '任务已取消'
            : noModels ? '暂无可用模型'
              : bundle ? (bundle.status === 'partial' ? '部分模型不可用' : '预测已生成')
                : latest.isPending ? '正在读取最近预测…' : noPrediction ? '暂无预测，请生成预测。' : '预测暂不可用'}
    </div>
    {error && <p className="error" role="alert">{predictionErrorText(error)}</p>}
    {possiblyStale && <p className="stale-notice">预测可能已过期：行情日期晚于预测基准。</p>}
    <PredictionSignals bundle={bundle} horizon={prediction} />
    <PredictionChart bundle={bundle} horizon={prediction} />
    {bundle && <ModelComparison bundle={bundle} horizon={prediction} />}
    {bundle?.warnings.length ? <div className="prediction-warnings">{bundle.warnings.map((warning, index) => <p key={`${index}-${warning}`}>{warning}</p>)}</div> : null}
    <details className="research-details">
      <summary><ChevronDown size={18} aria-hidden="true" />验证与数据</summary>
      <p>当前预测尚无经验证的样本外有效性结论。模型就绪不代表预测有效。</p>
      {bundle && <dl className="provenance"><dt>预测记录</dt><dd>{bundle.bundle_id}</dd><dt>数据快照</dt><dd>{bundle.snapshot_id}</dd>
        {Object.entries(bundle.components).map(([model, state]) => <div key={model}><dt>{model} 制品</dt><dd>{state.artifact_id ?? '--'}</dd></div>)}
      </dl>}
    </details>
  </section>
}