import { describe, expect, it } from 'vitest'
import { forecastCsv } from '../csv'
import { forecastFixture } from './fixtures'

describe('forecastCsv', () => {
  it('uses stable columns and TimesFM 3 q10/q90 values', () => {
    const csv = forecastCsv(forecastFixture)
    expect(csv.split('\n')[0]).toBe(
      'forecast_date,point_return,q10_return,q90_return,approx_point_price,approx_lower_price,approx_upper_price',
    )
    expect(csv.split('\n')[1]).toBe(
      '2024-07-01,0.01,-0.02,0.03,101.005,98.02,103.05',
    )
  })
})
