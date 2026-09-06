import { Download } from 'lucide-react'
import { useId, useState } from 'react'
import { ForecastPriceChart, ForecastReturnChart, HistoricalChart } from './Charts'
import { downloadForecastCsv } from './csv'
import { deviceNames, indices, messageText, priceColumns, sourceNames, valueUnit } from './i18n'
import type { BacktestResponse, ForecastResponse } from './types'

const chartViews = [{ id: 'price', label: '价格路径' }, { id: 'returns', label: '收益分布' },
  { id: 'history', label: '历史行情' }, { id: 'table', label: '逐日明细' }] as const

export function ForecastResults({ data }: { data: ForecastResponse }) {
  const [view, setView] = useState<typeof chartViews[number]['id']>('price')
  const id = useId()
  return <section className="research-results" aria-label="预测结果">
    <div className="result-heading"><h3>{data.history.name ?? data.ticker} · 未来 {data.summary.horizon} 日预测</h3>
      <button type="button" className="icon-button" title="导出预测表格" aria-label="导出预测表格" onClick={() => downloadForecastCsv(data)}><Download size={18} aria-hidden="true" /></button></div>
    <p className="data-meta">{sourceNames[data.history.source ?? 'yfinance']} · {priceColumns[data.history.price_column] ?? '收盘值'} · {valueUnit(data.history)} · {data.history.start} 至 {data.history.end}</p>
    <dl className="research-result-metrics">
      <div><dt>最近收盘值</dt><dd>{data.summary.latest_price.toFixed(2)}</dd></div>
      <div><dt>期末预测值</dt><dd>{data.summary.predicted_final_price.toFixed(2)}</dd></div>
      <div><dt>预测累计涨跌幅</dt><dd>{(data.summary.cumulative_return * 100).toFixed(2)}%</dd></div>
      <div><dt>期末近似区间</dt><dd>{data.summary.final_lower_price.toFixed(2)} 至 {data.summary.final_upper_price.toFixed(2)}</dd></div>
    </dl>
    <p className="data-meta">时序模型 · {deviceNames[data.model.device]} · 有效上下文 {data.context.used_length} · 基准 {data.context.origin}</p>
    <div className="result-views segments" role="radiogroup" aria-label="实验结果视图">
      {chartViews.map((item) => <label key={item.id}><input type="radio" name={`${id}-result-view`} checked={view === item.id} onChange={() => setView(item.id)} />{item.label}</label>)}
    </div>
    {view === 'history' && <HistoricalChart data={data} />}
    {view === 'price' && <ForecastPriceChart data={data} />}
    {view === 'returns' && <ForecastReturnChart data={data} />}
    {view === 'table' && <div className="table-wrap forecast-table"><table><caption>逐日预测（{valueUnit(data.history)}）</caption>
      <thead><tr><th>日期</th><th>预测值</th><th>预测涨跌幅</th><th>近似下界</th><th>近似上界</th></tr></thead>
      <tbody>{data.forecasts.map((point) => <tr key={point.date}><th scope="row">{point.date}</th><td>{point.point_price.toFixed(2)}</td><td>{(Math.expm1(point.point_return) * 100).toFixed(2)}%</td><td>{point.lower_price.toFixed(2)}</td><td>{point.upper_price.toFixed(2)}</td></tr>)}</tbody>
    </table></div>}
    <div className="warnings">{data.warnings.map((warning) => <p key={warning}>{messageText(warning)}</p>)}</div>
  </section>
}

export function BacktestResults({ data }: { data: BacktestResponse }) {
  const names: Record<string, string> = { timesfm: '时序模型', zero_return: '零收益基线', historical_mean: '历史均值基线', random_walk: '随机游走基线' }
  return <section className="research-results" aria-label="回测结果">
    <h3>{indices.find((item) => item.ticker === data.ticker)?.name ?? data.ticker} · 回测对照</h3>
    <p className="data-meta">{data.window_count} 个窗口 · {data.observation_count} 条样本</p>
    <p className="warnings">{messageText(data.warning)}</p>
    <div className="table-wrap backtest-table"><table><caption>模型与基线</caption>
      <thead><tr><th>模型</th><th>收益 MAE</th><th>收益 RMSE</th><th>方向准确率</th><th>价格 MAE</th><th>近似覆盖率</th></tr></thead>
      <tbody>{Object.entries(data.aggregate).map(([name, metric]) => <tr key={name}>
        <th scope="row">{names[name] ?? '基线模型'}</th><td>{metric.return_mae.toFixed(4)}</td><td>{metric.return_rmse.toFixed(4)}</td>
        <td>{(metric.directional_accuracy * 100).toFixed(1)}%</td><td>{metric.price_mae.toFixed(2)}</td><td>{metric.interval_coverage === null ? '--' : `${(metric.interval_coverage * 100).toFixed(1)}%`}</td>
      </tr>)}</tbody>
    </table></div>
  </section>
}