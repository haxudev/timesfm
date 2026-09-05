import { componentLabels, percent, signals } from './prediction'
import type { HorizonPrediction, PredictionBundle } from './types'

export function PredictionSignals({ bundle, horizon }: { bundle?: PredictionBundle; horizon?: HorizonPrediction }) {
  const signal = signals(bundle, horizon)
  return <dl className="prediction-signals">
    <div role="group" aria-label="预测累计收益">
      <dt>预测累计收益</dt>
      <dd className={signal.value !== null && signal.value > 0 ? 'price-up' : signal.value !== null && signal.value < 0 ? 'price-down' : ''}>{percent(signal.value, 2, true)}</dd>
      <small>{signal.source}</small>
    </div>
    <div role="group" aria-label="上涨概率">
      <dt>上涨概率</dt><dd>{percent(signal.probability, 1)}</dd>
      <small>{signal.probability === null ? '校准概率暂不可用' : 'LightGBM · 已校准'}</small>
    </div>
    <div role="group" aria-label="累计波动">
      <dt>累计波动</dt><dd>{percent(signal.volatility)}</dd>
      <small>{signal.volatility === null ? 'GARCH · 暂不可用' : 'GARCH · 风险幅度'}</small>
    </div>
  </dl>
}

export function ModelComparison({ bundle, horizon }: { bundle: PredictionBundle; horizon?: HorizonPrediction }) {
  const signal = signals(bundle, horizon)
  return <section className="model-comparison" aria-label="模型差异">
    <h3>模型差异</h3>
    <div className="model-lines">
      {(['timesfm', 'lightgbm', 'garch'] as const).map((model) => {
        const state = bundle.components[model]
        return <div className="model-line" key={model}>
          <strong>{model === 'timesfm' ? 'TimesFM' : model === 'lightgbm' ? 'LightGBM' : 'GARCH'}</strong>
          <span>{model === 'garch' ? '累计波动' : '累计收益'} {percent(signal[model === 'garch' ? 'volatility' : model], 2, model !== 'garch')}</span>
          <span className="model-state">{componentLabels[state.status]}</span>
          {state.reason && <p>{state.reason}</p>}
        </div>
      })}
    </div>
    <p className="data-meta">GARCH 衡量波动幅度，不预测涨跌方向。收益点预测不代表置信区间。</p>
  </section>
}