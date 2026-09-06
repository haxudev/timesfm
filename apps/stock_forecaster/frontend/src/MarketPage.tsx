import { useEffect, useRef } from 'react'
import { indices } from './i18n'
import { QuotePanel } from './QuotePanel'
import type { ResearchSymbol } from './types'

export function MarketPage({ selected, onSelect, active = true }: {
  selected: ResearchSymbol; onSelect: (symbol: ResearchSymbol) => void; active?: boolean
}) {
  const lastStock = useRef<ResearchSymbol>(selected.instrument_type === 'stock' ? selected : { ticker: '600519.SS', name: null, instrument_type: 'stock' })
  useEffect(() => { if (selected.instrument_type === 'stock') lastStock.current = selected }, [selected])
  const chooseType = (type: 'stock' | 'index') => {
    if (selected.instrument_type === 'stock') lastStock.current = selected
    onSelect(type === 'stock' ? lastStock.current : { ticker: '000001.SS', name: '上证指数', instrument_type: 'index' })
  }
  return <section className="market-page" aria-label="行情查看">
    <div className="page-title-row"><h2>行情查看</h2><span className="page-context">用户查看</span></div>
    <div className="market-selector">
      <div className="segments" role="radiogroup" aria-label="行情资产类型">
        <label><input type="radio" name="market-asset" checked={selected.instrument_type === 'stock'} onChange={() => chooseType('stock')} />个股</label>
        <label><input type="radio" name="market-asset" checked={selected.instrument_type === 'index'} onChange={() => chooseType('index')} />大盘指数</label>
      </div>
      {selected.instrument_type === 'index' && <label>指数<select value={selected.ticker} onChange={(event) => onSelect({ ticker: event.target.value,
        name: indices.find((item) => item.ticker === event.target.value)?.name ?? null, instrument_type: 'index' })}>
        {indices.map((item) => <option key={item.ticker} value={item.ticker}>{item.name}</option>)}
      </select></label>}
      <a className="inline-action" href="#prediction">查看预测</a>
    </div>
    <QuotePanel ticker={selected.ticker} active={active} />
  </section>
}