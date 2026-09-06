import type { Device, ForecastRequest, Target } from './types'

export interface ResearchForm {
  ticker: string
  assetType: 'stock' | 'index'
  indexTicker: string
  rangeMode: 'period' | 'dates'
  period: string
  start: string
  end: string
  horizon: number
  contextLength: number
  target: Target
  device: Device
}

export const initialResearchForm: ResearchForm = { ticker: '600519', assetType: 'stock', indexTicker: '000001.SS',
  rangeMode: 'period', period: '2y', start: '', end: '', horizon: 5, contextLength: 512, target: 'log_return', device: 'auto' }

export function researchRequest(form: ResearchForm): ForecastRequest {
  return { ticker: (form.assetType === 'index' ? form.indexTicker : form.ticker).trim().toUpperCase(),
    period: form.rangeMode === 'period' ? form.period : null,
    ...(form.rangeMode === 'dates' ? { start: form.start, end: form.end } : {}),
    horizon: form.horizon, context_length: form.contextLength, target: form.target, device: form.device }
}

export function validateResearch(form: ResearchForm): string | null {
  if (!/^[A-Za-z0-9^][A-Za-z0-9.^=-]{0,14}$/.test(researchRequest(form).ticker)) return '请输入有效的证券代码。'
  if (!Number.isInteger(form.horizon) || form.horizon < 1 || form.horizon > 60) return '预测天数必须为 1 至 60 的整数。'
  if (!Number.isInteger(form.contextLength) || form.contextLength < 32 || form.contextLength > 16384) return '上下文长度必须为 32 至 16,384 的整数。'
  if (form.rangeMode === 'dates' && (!form.start || !form.end || form.start >= form.end)) return '请选择有效的起止日期，结束日期不计入历史区间。'
  return null
}