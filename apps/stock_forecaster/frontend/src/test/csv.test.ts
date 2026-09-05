import { describe, expect, it } from 'vitest'
import { forecastCsv } from '../csv'
import { forecastFixture } from './fixtures'

describe('forecastCsv', () => {
  it('uses stable columns and TimesFM 3 q10/q90 values', () => {
    const csv = forecastCsv(forecastFixture)
    expect(csv.split('\n')[0]).toBe(
      '预测日期,预测对数收益率,收益率十分位数,收益率九十分位数,预测价格,近似下界,近似上界',
    )
    expect(csv.split('\n')[1]).toBe(
      '2024-07-01,0.01,-0.02,0.03,101.005,98.02,103.05',
    )
  })
})
