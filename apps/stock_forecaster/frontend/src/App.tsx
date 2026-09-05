import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiError } from './api'
import {
  ForecastPriceChart,
  ForecastReturnChart,
  HistoricalChart,
} from './Charts'
import { downloadForecastCsv } from './csv'
import { deviceNames, errorText, indices, messageText, modelStates, priceColumns, sourceNames, valueUnit } from './i18n'
import { QuotePanel } from './QuotePanel'
import { PredictionWorkspace } from './PredictionWorkspace'
import type {
  BacktestResponse,
  Device,
  ForecastRequest,
  Target,
} from './types'
import './styles.css'

const periods: Record<string, string> = { '1mo': '近一个月', '3mo': '近三个月', '6mo': '近半年', '1y': '近一年', '2y': '近两年', '5y': '近五年', '10y': '近十年', max: '全部历史' }
const horizonGroups = [
  { name: '超短期', days: [1, 3] },
  { name: '短期', days: [5, 10] },
  { name: '中期', days: [20, 60] },
]

interface FormState {
  ticker: string
  rangeMode: 'period' | 'dates'
  period: string
  start: string
  end: string
  horizon: number
  contextLength: number
  target: Target
  device: Device
}

const initialForm: FormState = {
  ticker: '600519',
  rangeMode: 'period',
  period: '2y',
  start: '',
  end: '',
  horizon: 5,
  contextLength: 512,
  target: 'log_return',
  device: 'auto',
}

function validate(form: FormState): string | null {
  if (!/^[A-Za-z0-9^][A-Za-z0-9.^=-]{0,14}$/.test(form.ticker.trim())) {
    return '请输入有效的证券代码。'
  }
  if (!Number.isInteger(form.horizon) || form.horizon < 1 || form.horizon > 60) return '预测天数必须为 1 至 60 的整数。'
  if (!Number.isInteger(form.contextLength) || form.contextLength < 32 || form.contextLength > 16384) {
    return '上下文长度必须为 32 至 16,384 的整数。'
  }
  if (form.rangeMode === 'dates' && (
    !form.start || !form.end || form.start >= form.end
  )) return '请选择有效的起止日期，结束日期不计入历史区间。'
  return null
}

function makeRequest(form: FormState): ForecastRequest {
  return {
    ticker: form.ticker.trim().toUpperCase(),
    period: form.rangeMode === 'period' ? form.period : null,
    ...(form.rangeMode === 'dates' ? { start: form.start, end: form.end } : {}),
    horizon: form.horizon,
    context_length: form.contextLength,
    target: form.target,
    device: form.device,
  }
}

function ErrorMessage({ error }: { error: Error | null }) {
  if (!error) return null
  const categories = { validation: '参数错误', network: '网络异常', model: '模型异常', server: '服务异常' }
  const category = error instanceof ApiError ? categories[error.kind] : '服务异常'
  return <p className="error" role="alert">{category}：{errorText(error)}</p>
}

export function AdvancedResearch() {
  const [form, setForm] = useState(initialForm)
  const [assetType, setAssetType] = useState<'stock' | 'index'>('stock')
  const [indexTicker, setIndexTicker] = useState('000001.SS')
  const selectedForm = { ...form, ticker: assetType === 'index' ? indexTicker : form.ticker }
  const [formError, setFormError] = useState<string | null>(null)
  const [windows, setWindows] = useState(10)
  const [stepSize, setStepSize] = useState(5)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 })
  const forecast = useMutation({ mutationFn: api.forecast })
  const backtest = useMutation({
    mutationFn: (request: ForecastRequest) => api.backtest({
      ...request,
      evaluation_windows: windows,
      step_size: stepSize,
    }),
  })

  const submitForecast = (event: FormEvent) => {
    event.preventDefault()
    const error = validate(selectedForm)
    setFormError(error)
    if (!error) forecast.mutate(makeRequest(selectedForm))
  }

  const submitBacktest = (event: FormEvent) => {
    event.preventDefault()
    const error = validate(selectedForm)
      ?? (!Number.isInteger(windows) || windows < 1 || windows > 100 ? '回测窗口数必须为 1 至 100 的整数。' : null)
      ?? (!Number.isInteger(stepSize) || stepSize < 1 || stepSize > 252 ? '滑动步长必须为 1 至 252 的整数。' : null)
    setFormError(error)
    if (!error) backtest.mutate(makeRequest(selectedForm))
  }

  return (
    <div className="advanced-research">
      <header>
        <div>
          <p className="eyebrow">大A市场 · 数据研究</p>
          <h1>牛来 · 大A 行情预测</h1>
        </div>
        <div className="health" aria-live="polite">
          <span className={health.data?.status === 'ok' ? 'dot ok' : 'dot'} />
          {health.isPending
            ? '正在连接后端…'
            : health.isError
              ? '后端不可用'
              : `${modelStates[health.data.model_state] ?? '状态未知'} · ${deviceNames[health.data.device] ?? '未知设备'}`}
        </div>
      </header>

      <section className="notice" aria-label="重要声明">
        <strong>不构成投资建议。</strong> 预测存在不确定性，仅供研究与学习，不保证盈利。
      </section>

      <section className="asset-selector" aria-label="预测标的">
        <div className="segments" role="group" aria-label="资产类型">
          <label><input type="radio" name="asset-type" checked={assetType === 'stock'} onChange={() => setAssetType('stock')} />个股</label>
          <label><input type="radio" name="asset-type" checked={assetType === 'index'} onChange={() => setAssetType('index')} />大盘指数</label>
        </div>
        {assetType === 'stock'
          ? <label>股票代码<input value={form.ticker} maxLength={15} onChange={(event) => setForm({ ...form, ticker: event.target.value })} /></label>
          : <label>指数<select value={indexTicker} onChange={(event) => setIndexTicker(event.target.value)}>{indices.map((index) => <option key={index.ticker} value={index.ticker}>{index.name}</option>)}</select></label>}
      </section>

      <QuotePanel ticker={selectedForm.ticker} />

      <form className="panel" onSubmit={submitForecast} noValidate>
        <h2>预测设置</h2>
        <div className="horizon-presets" role="group" aria-label="预测周期">
          {horizonGroups.map((group) => <fieldset key={group.name}><legend>{group.name}</legend><div className="segments">{group.days.map((days) => <label key={days}><input type="radio" name="horizon" checked={form.horizon === days} onChange={() => setForm({ ...form, horizon: days })} />{days} 日</label>)}</div></fieldset>)}
        </div>
        <div className="form-grid">
          <label>历史范围
            <select value={form.rangeMode} onChange={(event) => setForm({ ...form, rangeMode: event.target.value as FormState['rangeMode'] })}>
              <option value="period">预设区间</option>
              <option value="dates">指定日期</option>
            </select>
          </label>
          {form.rangeMode === 'period' ? (
            <label>历史区间<select value={form.period} onChange={(event) => setForm({ ...form, period: event.target.value })}>{Object.entries(periods).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          ) : (
            <>
              <label>开始日期<input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} /></label>
              <label>结束日期（不含）<input type="date" value={form.end} onChange={(event) => setForm({ ...form, end: event.target.value })} /></label>
            </>
          )}
          <label>预测天数<input type="number" min={1} max={60} step={1} value={form.horizon} onChange={(event) => setForm({ ...form, horizon: event.target.valueAsNumber })} /></label>
          <label>上下文长度<input type="number" min={32} max={16384} step={1} value={form.contextLength} onChange={(event) => setForm({ ...form, contextLength: event.target.valueAsNumber })} /></label>
          <label>预测目标<select value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value as Target })}><option value="log_return">对数收益率</option><option value="price">价格／点位（实验）</option></select></label>
          <label>计算设备<select value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as Device })}><option value="auto">自动选择</option><option value="cpu">处理器</option><option value="cuda">显卡</option></select></label>
        </div>
        <p className="data-meta">采样：日线 · 预测跨度：{form.horizon || '--'} 日 · 中国市场按交易日计</p>
        {formError && <p className="error" role="alert">{formError}</p>}
        <button type="submit" disabled={forecast.isPending}>{forecast.isPending ? '正在预测…' : '开始预测'}</button>
        <ErrorMessage error={forecast.error} />
      </form>

      {forecast.data && (
        <section aria-label="预测结果">
          <h2>{forecast.data.history.name ?? forecast.data.ticker} · 未来 {forecast.data.summary.horizon} 日预测</h2>
          <p className="data-meta">{sourceNames[forecast.data.history.source ?? 'yfinance']} · {priceColumns[forecast.data.history.price_column] ?? '收盘值'} · 单位：{valueUnit(forecast.data.history)} · {forecast.data.history.start} 至 {forecast.data.history.end}</p>
          <div className="summary-grid">
            <Summary label="最近收盘值" value={forecast.data.summary.latest_price.toFixed(2)} />
            <Summary label="期末预测值" value={forecast.data.summary.predicted_final_price.toFixed(2)} />
            <Summary label="预测累计涨跌幅" value={`${(forecast.data.summary.cumulative_return * 100).toFixed(2)}%`} />
            <Summary label="期末近似区间" value={`${forecast.data.summary.final_lower_price.toFixed(2)} 至 ${forecast.data.summary.final_upper_price.toFixed(2)}`} />
            <Summary label="预测日数／有效上下文" value={`${forecast.data.summary.horizon} / ${forecast.data.context.used_length}`} />
            <Summary label="模型／设备" value={`时序模型 · ${deviceNames[forecast.data.model.device]}`} />
          </div>
          <div className="warnings">{forecast.data.warnings.map((warning) => <p key={warning}>{messageText(warning)}</p>)}</div>
          <HistoricalChart data={forecast.data} />
          <ForecastPriceChart data={forecast.data} />
          <ForecastReturnChart data={forecast.data} />
          <div className="table-wrap forecast-table"><table><caption>逐日预测（{valueUnit(forecast.data.history)}）</caption>
            <thead><tr><th>日期</th><th>预测值</th><th>预测涨跌幅</th><th>近似下界</th><th>近似上界</th></tr></thead>
            <tbody>{forecast.data.forecasts.map((point) => <tr key={point.date}><th scope="row">{point.date}</th><td>{point.point_price.toFixed(2)}</td><td>{((Math.exp(point.point_return) - 1) * 100).toFixed(2)}%</td><td>{point.lower_price.toFixed(2)}</td><td>{point.upper_price.toFixed(2)}</td></tr>)}</tbody>
          </table></div>
          <button type="button" className="secondary" onClick={() => downloadForecastCsv(forecast.data)}>导出预测表格</button>
        </section>
      )}

      <form className="panel" onSubmit={submitBacktest} noValidate>
        <h2>滚动历史回测</h2>
        <div className="form-grid">
          <label>回测窗口数<input type="number" min={1} max={100} value={windows} onChange={(event) => setWindows(event.target.valueAsNumber)} /></label>
          <label>滑动步长（交易日）<input type="number" min={1} max={252} value={stepSize} onChange={(event) => setStepSize(event.target.valueAsNumber)} /></label>
        </div>
        <button type="submit" disabled={backtest.isPending}>{backtest.isPending ? '正在回测…' : '开始回测'}</button>
        <ErrorMessage error={backtest.error} />
        {backtest.data && <BacktestResults data={backtest.data} />}
      </form>
    </div>
  )
}

function Summary({ label, value }: { label: string; value: string }) {
  return <article className="summary"><span>{label}</span><strong>{value}</strong></article>
}

function BacktestResults({ data }: { data: BacktestResponse }) {
  const names: Record<string, string> = { timesfm: '时序模型', zero_return: '零收益基线', historical_mean: '历史均值基线', random_walk: '随机游走基线' }
  return (
    <div className="table-wrap">
      <p className="warnings">{messageText(data.warning)}</p>
      <table>
        <caption>{indices.find((index) => index.ticker === data.ticker)?.name ?? data.ticker} · {data.window_count} 个窗口 · {data.observation_count} 条样本</caption>
        <thead><tr><th>模型</th><th>收益率平均绝对误差</th><th>收益率均方根误差</th><th>方向准确率</th><th>价格／点位平均绝对误差</th><th>十分位至九十分位覆盖率</th></tr></thead>
        <tbody>{Object.entries(data.aggregate).map(([name, metric]) => (
          <tr key={name}><th>{names[name] ?? '基线模型'}</th><td>{metric.return_mae.toFixed(4)}</td><td>{metric.return_rmse.toFixed(4)}</td><td>{(metric.directional_accuracy * 100).toFixed(1)}%</td><td>{metric.price_mae.toFixed(2)}</td><td>{metric.interval_coverage === null ? '--' : `${(metric.interval_coverage * 100).toFixed(1)}%`}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export default function App() {
  return <PredictionWorkspace advanced={<AdvancedResearch />} />
}
