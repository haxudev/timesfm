import Plot from 'react-plotly.js'
import type { Data } from 'plotly.js'
import chineseLocale from 'plotly.js-locales/zh-cn'
import { finite, readyValue } from './prediction'
import type { HorizonPrediction, PredictionBundle } from './types'

export function PredictionChart({ bundle, horizon }: { bundle?: PredictionBundle; horizon?: HorizonPrediction }) {
  if (!bundle || !horizon) return <div className="prediction-chart-empty" aria-label="累计收益走势">暂无预测曲线</div>
  const originPrice = bundle.history.find((point) => point.date === bundle.origin)?.price
  const hasOrigin = finite(originPrice) && originPrice > 0
  const history = hasOrigin ? bundle.history.filter((point) => point.date <= bundle.origin && finite(point.price) && point.price > 0).sort((left, right) => left.date.localeCompare(right.date)).slice(-60) : []
  const path = bundle.components.timesfm.status === 'ready'
    ? bundle.path.filter((point) => point.date > bundle.origin && point.date <= horizon.target_date && finite(point.cumulative_return)).sort((left, right) => left.date.localeCompare(right.date)) : []
  const lightgbm = readyValue(bundle.components.lightgbm, horizon.lightgbm_return)
  const data: Data[] = []
  if (history.length) data.push({
    type: 'scatter', mode: 'lines', name: '历史（基准归零）',
    x: history.map((point) => point.date), y: history.map((point) => point.price / originPrice! - 1),
    line: { color: '#697a83', width: 2 },
    hovertemplate: '%{x}<br>相对基准 %{y:+.2%}<extra>历史</extra>',
  })
  if (path.length) data.push({
    type: 'scatter', mode: 'lines+markers', name: 'TimesFM 路径',
    x: [bundle.origin, ...path.map((point) => point.date)], y: [0, ...path.map((point) => point.cumulative_return)],
    line: { color: '#2364aa', width: 2.5 }, marker: { size: 4 },
    hovertemplate: '%{x}<br>累计收益 %{y:+.2%}<extra>TimesFM</extra>',
  })
  if (lightgbm !== null) data.push({
    type: 'scatter', mode: 'markers', name: 'LightGBM 目标',
    x: [horizon.target_date], y: [lightgbm],
    marker: { color: '#b17812', symbol: 'diamond', size: 11, line: { color: '#fff', width: 1 } },
    hovertemplate: '%{x}<br>累计收益 %{y:+.2%}<extra>LightGBM</extra>',
  })
  return <figure className="prediction-figure" aria-label="累计收益图">
    <Plot data={data} layout={{
      title: { text: '累计收益走势', x: 0, xanchor: 'left', font: { size: 15 } },
      autosize: true, margin: { l: 52, r: 14, t: 42, b: 74 },
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      font: { family: 'Microsoft YaHei, PingFang SC, sans-serif', color: '#536777', size: 11 },
      xaxis: { type: 'date', tickformat: '%m/%d', hoverformat: '%Y-%m-%d', nticks: 5, showgrid: false, zeroline: false },
      yaxis: { tickformat: '.1%', gridcolor: '#e5ebef', zerolinecolor: '#9baab7', automargin: true },
      legend: { orientation: 'h', x: 0, y: -0.18, font: { size: 10 } },
      hovermode: 'closest', dragmode: 'pan', uirevision: `${bundle.bundle_id}-${horizon.horizon}`,
      shapes: [{ type: 'line', x0: bundle.origin, x1: bundle.origin, y0: 0, y1: 1, yref: 'paper', line: { color: '#9baab7', dash: 'dot', width: 1 } }],
    }} config={{ responsive: true, displayModeBar: false, displaylogo: false, locale: 'zh-cn', locales: { 'zh-cn': chineseLocale }, scrollZoom: false }}
      useResizeHandler className="prediction-chart" />
    {!hasOrigin && <figcaption>缺少基准日价格，历史归零曲线不可用。</figcaption>}
    {!path.length && <figcaption>TimesFM 逐日预测路径暂不可用。</figcaption>}
  </figure>
}