import type { ForecastResponse } from './types'

const columns = [
  'forecast_date',
  'point_return',
  'q10_return',
  'q90_return',
  'approx_point_price',
  'approx_lower_price',
  'approx_upper_price',
] as const

export function forecastCsv(data: ForecastResponse): string {
  const rows = data.forecasts.map((point) => [
    point.date,
    point.point_return,
    point.quantiles['q0.1'],
    point.quantiles['q0.9'],
    point.point_price,
    point.lower_price,
    point.upper_price,
  ].join(','))
  return [columns.join(','), ...rows].join('\n')
}

export function downloadForecastCsv(data: ForecastResponse): void {
  const url = URL.createObjectURL(new Blob([forecastCsv(data)], { type: 'text/csv' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${data.ticker.toLowerCase()}-forecast.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}
