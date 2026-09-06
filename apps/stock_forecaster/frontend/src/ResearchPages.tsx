import { useMutation, useQuery } from '@tanstack/react-query'
import { ChartNoAxesCombined, Play } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from './api'
import { deviceNames, errorText, modelStates } from './i18n'
import { ResearchParameters } from './ResearchParameters'
import { initialResearchForm, researchRequest, validateResearch } from './researchForm'
import { BacktestResults, ForecastResults } from './ResearchResults'
import type { ForecastRequest } from './types'

function ResearchError({ error }: { error: Error | null }) {
  if (!error) return null
  const categories = { validation: '参数错误', network: '网络异常', model: '模型异常', server: '服务异常' }
  return <p className="error" role="alert">{error instanceof ApiError ? categories[error.kind] : '服务异常'}：{errorText(error)}</p>
}

function ExperimentPage({ kind, active }: { kind: 'forecast' | 'backtest'; active: boolean }) {
  const [form, setForm] = useState(initialResearchForm)
  const [windows, setWindows] = useState(10)
  const [stepSize, setStepSize] = useState(5)
  const [formError, setFormError] = useState<string | null>(null)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, enabled: active, refetchInterval: active ? 30_000 : false })
  const forecast = useMutation({ mutationFn: api.forecast })
  const backtest = useMutation({ mutationFn: (request: ForecastRequest & { evaluation_windows: number; step_size: number }) => api.backtest(request) })
  const isForecast = kind === 'forecast'
  const running = isForecast ? forecast.isPending : backtest.isPending
  const title = isForecast ? '预测实验' : '历史回测'
  const submitted = isForecast ? forecast.variables : backtest.variables
  const hasResult = isForecast ? Boolean(forecast.data) : Boolean(backtest.data)
  const currentRequest = { ...researchRequest(form), ...(!isForecast ? { evaluation_windows: windows, step_size: stepSize } : {}) }
  const changed = hasResult && submitted && JSON.stringify(submitted) !== JSON.stringify(currentRequest)
  return <section className="research-page" aria-label={title}>
    <div className="page-title-row"><h2>{title}</h2><div className="health" aria-live="polite"><span className={health.data?.status === 'ok' ? 'dot ok' : 'dot'} />
      {health.isPending ? '正在连接后端…' : health.isError ? '后端不可用' : `${modelStates[health.data.model_state] ?? '状态未知'} · ${deviceNames[health.data.device] ?? '未知设备'}`}
    </div></div>
    <p className="experiment-notice">研究管理 · 本机实验，未批准上线。不构成投资建议。</p>
    <div className="research-page-grid">
      <form className="research-config" noValidate onSubmit={(event) => {
        event.preventDefault()
        const error = validateResearch(form)
          ?? (!isForecast && (!Number.isInteger(windows) || windows < 1 || windows > 100) ? '回测窗口数必须为 1 至 100 的整数。' : null)
          ?? (!isForecast && (!Number.isInteger(stepSize) || stepSize < 1 || stepSize > 252) ? '滑动步长必须为 1 至 252 的整数。' : null)
        setFormError(error)
        if (error || running) return
        if (isForecast) forecast.mutate(researchRequest(form))
        else backtest.mutate({ ...researchRequest(form), evaluation_windows: windows, step_size: stepSize })
      }}>
        <ResearchParameters form={form} onChange={setForm} disabled={running} />
        {!isForecast && <fieldset className="research-parameters backtest-parameters" disabled={running}><legend>滚动窗口</legend>
          <div className="research-field-pair"><label>回测窗口数<input type="number" min={1} max={100} value={Number.isFinite(windows) ? windows : ''} onChange={(event) => setWindows(event.target.valueAsNumber)} /></label>
            <label>滑动步长（交易日）<input type="number" min={1} max={252} value={Number.isFinite(stepSize) ? stepSize : ''} onChange={(event) => setStepSize(event.target.valueAsNumber)} /></label></div>
        </fieldset>}
        {formError && <p className="error" role="alert">{formError}</p>}
        <p className="data-meta">日线 · {form.horizon || '--'} 个交易日</p>
        <button className="research-run" type="submit" disabled={running}><Play size={16} aria-hidden="true" />{isForecast ? running ? '正在预测…' : '开始预测' : running ? '正在回测…' : '开始回测'}</button>
        <ResearchError error={isForecast ? forecast.error : backtest.error} />
      </form>
      <div className="research-output" aria-busy={running}>
        {running && <p role="status" className="research-running">{isForecast ? '预测实验运行中' : '历史回测运行中'}</p>}
        {hasResult && submitted && <div className="result-input-snapshot">
          <p className="data-meta" aria-label="结果参数">结果参数：{submitted.ticker} · {submitted.horizon} 日 · 上下文 {submitted.context_length} · {submitted.period ?? `${submitted.start} 至 ${submitted.end}`} · {submitted.target === 'price' ? '价格／点位' : '对数收益率'} · {deviceNames[submitted.device]}
            {!isForecast && backtest.variables && ` · ${backtest.variables.evaluation_windows} 个窗口 · 步长 ${backtest.variables.step_size}`}</p>
          {changed && <p className="stale-notice" role="status">参数已变更，当前结果仍对应上次提交。</p>}
        </div>}
        {isForecast && forecast.data ? <ForecastResults data={forecast.data} /> : !isForecast && backtest.data ? <BacktestResults data={backtest.data} />
          : <div className="research-empty"><ChartNoAxesCombined size={36} strokeWidth={1.2} aria-hidden="true" /><h3>{isForecast ? '暂无预测实验结果' : '暂无历史回测结果'}</h3></div>}
      </div>
    </div>
  </section>
}

export function ForecastExperimentPage({ active = true }: { active?: boolean }) {
  return <ExperimentPage kind="forecast" active={active} />
}

export function HistoricalBacktestPage({ active = true }: { active?: boolean }) {
  return <ExperimentPage kind="backtest" active={active} />
}