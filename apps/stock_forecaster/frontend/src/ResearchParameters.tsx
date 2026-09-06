import { useId } from 'react'
import { indices } from './i18n'
import type { Device, Target } from './types'
import type { ResearchForm } from './researchForm'

const periods = { '1mo': '近一个月', '3mo': '近三个月', '6mo': '近半年', '1y': '近一年', '2y': '近两年', '5y': '近五年', '10y': '近十年', max: '全部历史' }

export function ResearchParameters({ form, onChange, disabled }: { form: ResearchForm; onChange: (form: ResearchForm) => void; disabled: boolean }) {
  const id = useId()
  const change = (values: Partial<ResearchForm>) => onChange({ ...form, ...values })
  return <fieldset className="research-parameters" disabled={disabled}>
    <legend>参数设置</legend>
    <div className="segments" role="radiogroup" aria-label="资产类型">
      <label><input type="radio" name={`${id}-asset`} checked={form.assetType === 'stock'} onChange={() => change({ assetType: 'stock' })} />个股</label>
      <label><input type="radio" name={`${id}-asset`} checked={form.assetType === 'index'} onChange={() => change({ assetType: 'index' })} />大盘指数</label>
    </div>
    {form.assetType === 'stock' ? <label>股票代码<input maxLength={15} value={form.ticker} onChange={(event) => change({ ticker: event.target.value })} /></label>
      : <label>指数<select value={form.indexTicker} onChange={(event) => change({ indexTicker: event.target.value })}>
        {indices.map((item) => <option key={item.ticker} value={item.ticker}>{item.name}</option>)}
      </select></label>}
    <div className="research-field-pair">
      <label>历史范围<select value={form.rangeMode} onChange={(event) => change({ rangeMode: event.target.value as ResearchForm['rangeMode'] })}>
        <option value="period">预设区间</option><option value="dates">指定日期</option>
      </select></label>
      {form.rangeMode === 'period' && <label>历史区间<select value={form.period} onChange={(event) => change({ period: event.target.value })}>
        {Object.entries(periods).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select></label>}
    </div>
    {form.rangeMode === 'dates' && <div className="research-field-pair">
      <label>开始日期<input type="date" value={form.start} onChange={(event) => change({ start: event.target.value })} /></label>
      <label>结束日期（不含）<input type="date" value={form.end} onChange={(event) => change({ end: event.target.value })} /></label>
    </div>}
    <fieldset className="research-horizons"><legend>预测期限</legend><div className="segments" role="radiogroup" aria-label="预测期限">
      {[1, 3, 5, 10, 20, 60].map((days) => <label key={days}><input type="radio" name={`${id}-horizon`} checked={form.horizon === days} onChange={() => change({ horizon: days })} />{days} 日</label>)}
      <label className="custom-horizon"><input type="radio" name={`${id}-horizon`} checked={![1, 3, 5, 10, 20, 60].includes(form.horizon)} onChange={() => change({ horizon: 7 })} />自定义</label>
    </div></fieldset>
    <div className="research-field-pair">
      <label>预测天数<input type="number" min={1} max={60} step={1} value={Number.isFinite(form.horizon) ? form.horizon : ''} onChange={(event) => change({ horizon: event.target.valueAsNumber })} /></label>
      <label>上下文长度<input type="number" min={32} max={16384} step={1} value={Number.isFinite(form.contextLength) ? form.contextLength : ''} onChange={(event) => change({ contextLength: event.target.valueAsNumber })} /></label>
    </div>
    <label>预测目标<select value={form.target} onChange={(event) => change({ target: event.target.value as Target })}>
      <option value="log_return">对数收益率</option><option value="price">价格／点位（实验）</option>
    </select></label>
    <label>计算设备<select value={form.device} onChange={(event) => change({ device: event.target.value as Device })}>
      <option value="auto">自动选择</option><option value="cpu">处理器</option><option value="cuda">显卡</option>
    </select></label>
  </fieldset>
}