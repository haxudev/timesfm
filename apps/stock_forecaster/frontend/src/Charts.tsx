import Plot from 'react-plotly.js'
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
        name: `${data.history.price_column} price`,
        line: { color: '#2364aa' },
      }]}
      layout={{ ...layout, title: { text: 'Historical price' }, yaxis: { title: { text: 'Price' } } }}
      useResizeHandler
      className="chart"
      config={{ responsive: true, displaylogo: false }}
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
          name: 'Approx. q80',
        },
        {
          x: dates,
          y: q20Prices,
          type: 'scatter',
          mode: 'lines',
          fill: 'tonexty',
          fillcolor: 'rgba(0, 114, 178, 0.18)',
          line: { width: 0 },
          name: 'Approx. q20–q80',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.upper_price),
          type: 'scatter',
          mode: 'lines',
          line: { width: 0 },
          hoverinfo: 'skip',
          showlegend: false,
          name: 'Approx. q90',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.lower_price),
          type: 'scatter',
          mode: 'lines',
          fill: 'tonexty',
          fillcolor: 'rgba(230, 159, 0, 0.22)',
          line: { width: 0 },
          name: 'Approx. q10–q90',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.point_price),
          type: 'scatter',
          mode: 'lines+markers',
          line: { color: '#d55e00' },
          name: 'Point forecast',
        },
        {
          x: history.map((item) => item.date),
          y: history.map((item) => item.price),
          type: 'scatter',
          mode: 'lines',
          line: { color: '#2364aa' },
          name: 'Recent history',
        },
      ]}
      layout={{
        ...layout,
        title: { text: 'Approximate forecast price path' },
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
      config={{ responsive: true, displaylogo: false }}
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
          name: 'q10–q90',
        },
        {
          x: dates,
          y: data.forecasts.map((item) => item.point_return),
          type: 'scatter',
          mode: 'lines+markers',
          line: { color: '#0072b2' },
          name: 'Point return',
        },
      ]}
      layout={{
        ...layout,
        title: { text: 'Forecast log returns' },
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
      config={{ responsive: true, displaylogo: false }}
    />
  )
}
