import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ChevronDown, X } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { ModelComparison, PredictionSignals } from './PredictionSignals'
import { PredictionChart } from './PredictionChart'
import { predictionDisplay, predictionErrorText, signals } from './prediction'
import { SymbolSearch } from './SymbolSearch'
import type { PredictionHorizon, ResearchSymbol } from './types'

const defaultStock: ResearchSymbol = { ticker: '600519.SS', name: null, instrument_type: 'stock' }

export function PredictionWorkspace({ advanced }: { advanced: ReactNode }) {
  const [selected, setSelected] = useState(defaultStock)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  return <main className="prediction-workspace">
    <header className="workspace-header">
      <div className="brand"><h1>牛来</h1><span>大A · 个股预测</span></div>
      <SymbolSearch selected={selected} onSelect={setSelected} />
    </header>
    <PredictionSession key={selected.ticker} symbol={selected} />
    <details className="research-details" onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
      <summary><ChevronDown size={18} aria-hidden="true" />高级研究</summary>
      {advancedOpen && advanced}
    </details>
    <footer className="workspace-footer">不构成投资建议。预测存在不确定性，不保证盈利。</footer>
  </main>
}

function PredictionSession({ symbol }: { symbol: ResearchSymbol }) {
  const { ticker } = symbol
  const [horizon, setHorizon] = useState<PredictionHorizon>(5)
  const client = useQueryClient()
  const controller = useRef<AbortController | null>(null)
  const latestKey = ['prediction', ticker, 'latest']
  const latest = useQuery({ queryKey: latestKey, queryFn: ({ signal }) => api.latestPrediction(ticker, signal), retry: false, refetchOnWindowFocus: false })
  const quote = useQuery({ queryKey: ['compact-quote', ticker], queryFn: ({ signal }) => api.quote(ticker, 'auto', signal), retry: false, refetchOnWindowFocus: false })
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
      <p>暂无样本外验证数据。模型就绪不代表预测已经通过有效性验证。</p>
      {bundle && <dl className="provenance"><dt>预测记录</dt><dd>{bundle.bundle_id}</dd><dt>数据快照</dt><dd>{bundle.snapshot_id}</dd>
        {Object.entries(bundle.components).map(([model, state]) => <div key={model}><dt>{model} 制品</dt><dd>{state.artifact_id ?? '--'}</dd></div>)}
      </dl>}
    </details>
  </section>
}