import type { MarketData } from './types'

export const indices = [
  { ticker: '000001.SS', name: '上证指数' },
  { ticker: '399001.SZ', name: '深证成指' },
  { ticker: '399006.SZ', name: '创业板指' },
  { ticker: '000300.SS', name: '沪深300' },
  { ticker: '000905.SS', name: '中证500' },
  { ticker: '000688.SS', name: '科创50' },
]

export const deviceNames: Record<string, string> = {
  auto: '自动选择', cpu: '处理器', cuda: '显卡', unavailable: '不可用',
}
export const modelStates: Record<string, string> = {
  not_loaded: '待加载', loading: '正在加载', ready: '已就绪',
  disabled: '已停用', error: '加载异常',
}
export const sourceNames: Record<string, string> = {
  tencent: 'TX', sina: 'XL', 'akshare/tencent': 'TX历史日线',
  yfinance: '雅虎财经',
}
export const priceColumns: Record<string, string> = {
  'Adj Close': '复权收盘价', Close: '收盘值',
}

export function valueUnit(history: MarketData): string {
  if (history.instrument_type === 'index') return '点'
  return ({ CNY: '元', USD: '美元', HKD: '港元', EUR: '欧元' } as Record<string, string>)[history.currency ?? ''] ?? '报价单位'
}

const messages: Record<string, string> = {
  'CUDA is unavailable; inference is using CPU.': '显卡计算不可用，本次使用处理器推理。',
  'Tencent unavailable; using Sina without valuations.': 'TX行情不可用，已切换XL；估值数据暂缺。',
  'Historical evaluation does not imply future performance or profitability.': '历史回测不代表未来表现或盈利能力。',
  'Historical evaluation does not imply future performance.': '历史回测不代表未来表现。',
  'TimesFM 3 truncated each context to 15,360 values.': '时序模型 已将每个上下文截断至 15,360 条。',
  'Approximate paths are not joint confidence intervals.': '近似预测路径不代表联合置信区间。',
  'Future dates use business days; exchange-specific holidays may be absent.': '预测日期暂按工作日生成，可能未排除交易所休市日。',
  'Accumulated marginal return quantiles are approximate price paths, not joint path confidence intervals.': '收益率分位数累积形成近似路径，不代表联合置信区间。',
  'Direct price forecasting is experimental.': '直接预测价格或点位为实验功能。',
  'TimesFM 3 uses at most 15,360 context values; the context was truncated.': '时序模型 最多使用 15,360 条上下文，超出部分已截断。',
}

export function messageText(message: string): string {
  const translated = Object.entries(messages).reduce(
    (text, [english, chinese]) => text.replaceAll(english, chinese), message,
  )
    .replace(/TimesFM\s*3(?:\.0)?\b/g, '时序模型')
    .replaceAll('腾讯', 'TX')
    .replaceAll('新浪', 'XL')
  return /[\u4e00-\u9fff]/.test(translated) ? translated : '服务暂不可用，请稍后重试。'
}

const errorMessages: Record<string, string> = {
  invalid_ticker: '证券代码无效，请检查市场前缀和代码。',
  validation_error: '参数不符合要求，请检查日期范围、预测天数和上下文长度。',
  quote_unsupported: '该代码暂不支持中国市场行情快照。',
  quote_unavailable: '行情暂不可用，请切换来源或稍后重试。',
  market_data_unavailable: '历史行情取数失败或超时，请稍后重试。',
  market_data_empty: '所选区间没有有效历史数据，请扩大时间范围。',
  market_data_malformed: '上游行情缺少有效收盘值。',
  insufficient_history: '历史样本不足，请延长历史范围或缩短上下文及回测窗口。',
  non_finite_prices: '历史行情包含无效数值，暂时无法预测。',
  non_positive_prices: '价格必须为正数，当前数据无法计算对数收益率。',
  device_unavailable: '显卡计算不可用，请选择处理器或自动选择。',
  model_disabled: '服务器已停用预测模型。',
  model_load_failed: '模型加载失败，请检查权重下载、依赖和可用内存。',
  model_inference_failed: '模型推理失败，请缩短上下文或稍后重试。',
  model_output_invalid: '模型返回无效预测结果，请调整参数后重试。',
  model_capacity_exceeded: '模型正在处理其他请求，请稍后重试。',
  calendar_unavailable: '预测日期超出已发布交易日历范围，请缩短周期或更新日历。',
  internal_error: '服务暂时无法处理请求，请稍后重试。',
}

export function errorText(error: Error): string {
  const code = 'code' in error && typeof error.code === 'string' ? error.code : ''
  return errorMessages[code] ?? messageText(error.message)
}