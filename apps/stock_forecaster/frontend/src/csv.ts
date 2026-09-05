import type { ForecastResponse } from './types'

const columns = [
  '预测日期',
  '预测对数收益率',
  '收益率十分位数',
  '收益率九十分位数',
  '预测价格',
  '近似下界',
  '近似上界',
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
  const header = columns.map((column) => column === '预测价格' && data.history.instrument_type === 'index' ? '预测点位' : column)
  return [header.join(','), ...rows].join('\n')
}

export function downloadForecastCsv(data: ForecastResponse): void {
  const url = URL.createObjectURL(new Blob(['\uFEFF', forecastCsv(data)], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${data.ticker}-预测结果.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}
