import Plot from 'react-plotly.js'
import type { Config } from 'plotly.js'
import chineseLocale from 'plotly.js-locales/zh-cn'
import { priceColumns, valueUnit } from './i18n'
import type { ForecastResponse } from './types'

function accumulatedQuantilePrices(data: ForecastResponse, name: string): number[] {
  let cumulative = 0
  return data.forecasts.map((point) => {
    cumulative += point.quantiles[name]
    return data.summary.latest_price * Math.exp(cumulative)
  })
}

const layout = {
  autosize: true,
  margin: { l: 55, r: 20, t: 45, b: 50 },
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  legend: { orientation: 'h' as const },
  font: { family: 'Microsoft YaHei, sans-serif' },
  xaxis: { tickformat: '%m月%d日', hoverformat: '%Y年%m月%d日' },
}

const chartConfig: Partial<Config> = {
  responsive: true, displaylogo: false, locale: 'zh-cn',
  locales: { 'zh-cn': chineseLocale },
  toImageButtonOptions: { filename: '预测图表' },
  modeBarButtons: [
    ['toImage'],
    ['zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
    ['select2d', 'lasso2d'],
  ],
}

export function HistoricalChart({ data }: { data: ForecastResponse }) {
  const recent = data.history.observations.slice(-120)
  return (
    <Plot
      data={[{
        x: recent.map((item) => item.date),
        y: recent.map((item) => item.price),
        type: 'scatter',
        mode: 'lines',
        name: priceColumns[data.history.price_column] ?? '历史收盘值',
        line: { color: '#2364aa' },
      }]}
      layout={{ ...layout, title: { text: '历史走势' }, yaxis: { title: { text: valueUnit(data.history) } } }}
      useResizeHandler
      className="chart"
      config={chartConfig}
    />
  )
}

export function ForecastPriceChart({ data }: { data: ForecastResponse }) {
  const dates = data.forecasts.map((item) => item.date)
  const origin = data.context.origin
  const history = data.history.observations.slice(-60)
  const q20Prices = accumulatedQuantilePrices(data, 'q0.2')
  const q80Prices = accumulatedQuantilePrices(data, 'q0.8')
  return (
    <Plot
      data={[
        {
          x: dates,
          y: q80Prices,
          type: 'scatter',
          mode: 'lines',
          line: { width: 0 },
          hoverinfo: 'skip',
          showlegend: false,
          name: '近似八十分位数',
        },
        {
          x: dates,
          y: q20Prices,
          type: 'scatter',
          mode: 'lines',
          fill: 'tonexty',
          fillcolor: 'rgba(0, 114, 178, 0.18)',
          line: { width: 0 },
          name: '近似二十至八十分位区间',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.upper_price),
          type: 'scatter',
          mode: 'lines',
          line: { width: 0 },
          hoverinfo: 'skip',
          showlegend: false,
          name: '近似九十分位数',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.lower_price),
          type: 'scatter',
          mode: 'lines',
          fill: 'tonexty',
          fillcolor: 'rgba(230, 159, 0, 0.22)',
          line: { width: 0 },
          name: '近似十分位至九十分位区间',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.point_price),
          type: 'scatter',
          mode: 'lines+markers',
          line: { color: '#d55e00' },
          name: '预测值',
        },
        {
          x: history.map((item) => item.date),
          y: history.map((item) => item.price),
          type: 'scatter',
          mode: 'lines',
          line: { color: '#2364aa' },
          name: '近期历史',
        },
      ]}
      layout={{
        ...layout,
        title: { text: '预测走势与近似区间' },
        yaxis: { title: { text: valueUnit(data.history) } },
        shapes: [{
          type: 'line',
          x0: origin,
          x1: origin,
          y0: 0,
          y1: 1,
          yref: 'paper',
          line: { color: '#555', dash: 'dot' },
        }],
      }}
      useResizeHandler
      className="chart"
      config={chartConfig}
    />
  )
}

export function ForecastReturnChart({ data }: { data: ForecastResponse }) {
  const dates = data.forecasts.map((item) => item.date)
  return (
    <Plot
      data={[
        {
          x: dates,
          y: data.forecasts.map((item) => item.quantiles['q0.9']),
          type: 'scatter',
          mode: 'lines',
          line: { width: 0 },
          hoverinfo: 'skip',
          showlegend: false,
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.quantiles['q0.1']),
          type: 'scatter',
          mode: 'lines',
          fill: 'tonexty',
          fillcolor: 'rgba(0, 158, 115, 0.2)',
          line: { width: 0 },
          name: '十分位至九十分位区间',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.point_return),
          type: 'scatter',
          mode: 'lines+markers',
          line: { color: '#0072b2' },
          name: '预测对数收益率',
        },
      ]}
      layout={{
        ...layout,
        title: { text: '预测对数收益率' },
        yaxis: { tickformat: '.2%' },
        shapes: [{
          type: 'line',
          x0: dates[0],
          x1: dates.at(-1),
          y0: 0,
          y1: 0,
          line: { color: '#555', dash: 'dot' },
        }],
      }}
      useResizeHandler
      className="chart"
      config={chartConfig}
    />
  )
}
