import { errorText } from './i18n'
import type { ComponentState, HorizonPrediction, PredictionBundle } from './types'

const reasonMessages = new Map([
  ['artifact_missing', '尚无可用模型制品。'],
  ['stock_models_only', '该模型仅适用于个股，不适用于指数。'],
  ['feature_schema_missing', '模型缺少特征结构信息。'],
  ['trained_through_required', '模型缺少训练截止日期。'],
  ['artifact_after_origin', '模型训练或拟合日期晚于预测基准，不能用于本次预测。'],
  ['trusted_features_required', '缺少可信的历史时点特征。'],
  ['feature_snapshot_invalid', '特征快照无效。'],
  ['dependency_unavailable', '模型运行依赖暂不可用。'],
  ['artifact_or_features_invalid', '模型制品或特征数据校验未通过。'],
  ['artifact_provenance_missing', '模型缺少可追溯的拟合记录。'],
  ['fit_snapshot_unavailable', '模型拟合所用的数据快照暂不可用。'],
  ['insufficient_garch_history', 'GARCH 至少需要 252 个有效日收益样本。'],
  ['artifact_invalid', '模型制品校验未通过。'],
  ['non_continuous_sessions', '历史数据缺少交易日，暂时无法预测。'],
  ['snapshot_mismatch', '价格数据与已保存的快照不一致，请重新提交预测。'],
  ['configuration_changed', '任务配置已变化，请重新提交预测。'],
  ['artifact_changed', '模型版本已变化，请重新提交预测。'],
  ['idempotency_conflict', '该任务标识已用于其他预测，请重新提交。'],
  ['job_not_found', '未找到预测任务，请重新提交。'],
  ['job_terminal', '任务已结束，无法再取消。'],
  ['job_inactive', '任务已取消或执行租约已过期。'],
  ['prediction_not_found', '暂无已保存的预测。'],
])

const warningMessages = new Map([
  ['Frozen current-vintage prices are not PIT corporate-action data; replay does not restore historical adjustments.', '已冻结本次获取版本的价格，但并非历史时点的公司行动数据；重放无法还原当时的复权调整。'],
  ['TimesFM checkpoint name is recorded; an immutable weight revision is not pinned.', 'TimesFM 仅记录了权重名称，尚未固定不可变的权重版本。'],
  ['Price adjustment provenance is unknown for this provider.', '该数据源的价格复权依据尚不明确。'],
  ['The available history ends before the latest completed session.', '可用历史数据尚未覆盖最近一个已结束的交易日。'],
])

const unavailableMessage = '服务暂不可用，请稍后重试。'

export function predictionErrorText(error: Error | { code?: string } | null | undefined): string {
  if (error && 'kind' in error && error.kind === 'network') {
    return 'message' in error && error.message === '请求超时，请稍后重试。'
      ? '请求超时，请稍后重试。' : '无法连接后端服务，请检查服务是否已启动。'
  }
  const code = error && 'code' in error && typeof error.code === 'string' ? error.code : ''
  const translated = reasonMessages.get(code) ?? errorText(Object.assign(new Error(''), { code }))
  return typeof translated === 'string' ? translated : unavailableMessage
}

function componentReasonText(reason: string | null): string | null {
  if (!reason) return null
  if (reason.length > 256) return unavailableMessage
  if (/^(1|5|20):[a-z_]+(?:;(1|5|20):[a-z_]+){0,2}$/.test(reason)) {
    return reason.split(';').map((part) => {
      const [horizon, code] = part.split(':')
      return `${horizon} 日：${predictionErrorText({ code })}`
    }).join('；')
  }
  return predictionErrorText({ code: reason })
}

export function predictionDisplay(bundle: PredictionBundle): PredictionBundle {
  return {
    ...bundle,
    components: Object.fromEntries(Object.entries(bundle.components).map(([model, state]) => [
      model, { ...state, reason: componentReasonText(state.reason) },
    ])) as PredictionBundle['components'],
    warnings: bundle.warnings.map((warning) => warningMessages.get(warning)
      ?? '存在未识别的数据或模型警告，请谨慎使用本次预测。'),
  }
}

export function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function percent(value: number | null | undefined, digits = 2, signed = false) {
  if (!finite(value)) return '--'
  return `${signed && value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

export function readyValue(state: ComponentState | undefined, value: number | null | undefined) {
  return state?.status === 'ready' && finite(value) ? value : null
}

export function signals(bundle?: PredictionBundle, horizon?: HorizonPrediction) {
  const timesfm = readyValue(bundle?.components.timesfm, horizon?.timesfm_return)
  const lightgbm = readyValue(bundle?.components.lightgbm, horizon?.lightgbm_return)
  const probability = readyValue(bundle?.components.lightgbm, horizon?.up_probability)
  const volatility = readyValue(bundle?.components.garch, horizon?.volatility)
  return {
    timesfm, lightgbm,
    value: lightgbm ?? timesfm,
    source: lightgbm !== null ? 'LightGBM' : timesfm !== null ? 'TimesFM · 替代点预测' : '暂无收益信号',
    probability: probability !== null && probability >= 0 && probability <= 1 ? probability : null,
    volatility: volatility !== null && volatility >= 0 ? volatility : null,
  }
}

export const componentLabels: Record<ComponentState['status'], string> = {
  ready: '已就绪', not_ready: '尚未就绪', unavailable: '不可用', not_supported: '不适用',
}