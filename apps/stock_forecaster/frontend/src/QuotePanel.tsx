import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api } from './api'
import { errorText, messageText, sourceNames } from './i18n'
import type { QuoteSource } from './types'

const aSharePattern = /^(?:[03648]\d{5}|92\d{4}|(?:sh|sz|bj)\d{6}|\d{6}\.(?:ss|sh|sz|bj))$/i
const timestampFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium',
})

function number(value: number | null, digits = 2): string {
  return value === null ? '--' : value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })
}

function compact(value: number | null): string {
  return value === null ? '--' : value.toLocaleString('zh-CN', {
    notation: 'compact', maximumFractionDigits: 2,
  })
}

export function QuotePanel({ ticker, active = true }: { ticker: string; active?: boolean }) {
  const [source, setSource] = useState<QuoteSource>('auto')
  const [polling, setPolling] = useState(true)
  const symbol = ticker.trim().toUpperCase()
  const supported = aSharePattern.test(symbol)
  const quote = useQuery({
    queryKey: ['quote', symbol, source],
    queryFn: () => api.quote(symbol, source),
    enabled: supported && active,
    refetchInterval: polling && active ? 10_000 : false,
    refetchIntervalInBackground: false,
    staleTime: 5_000,
    retry: false,
  })
  if (!supported) return null
  const data = quote.data
  const isIndex = data?.instrument_type === 'index'
  const direction = (data?.change ?? 0) > 0 ? 'price-up'
    : (data?.change ?? 0) < 0 ? 'price-down' : ''

  return (
    <section className="quote-panel" aria-label="行情快照">
      <div className="quote-toolbar">
        <h2>{isIndex ? '大盘行情' : '个股行情'}</h2>
        <div className="quote-controls">
          <label>行情来源
            <select value={source} onChange={(event) => setSource(event.target.value as QuoteSource)}>
              <option value="auto">自动：TX优先，XL备用</option>
              <option value="tencent">{sourceNames.tencent}</option>
              <option value="sina">{sourceNames.sina}</option>
            </select>
          </label>
          <label className="poll-toggle">
            <input type="checkbox" checked={polling} onChange={(event) => setPolling(event.target.checked)} />
            自动刷新（10 秒）
          </label>
          <button type="button" className="secondary icon-button" aria-label="刷新行情" title="刷新行情" disabled={quote.isFetching} onClick={() => void quote.refetch()}>
            <RefreshCw size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
      {quote.isPending && <p role="status">正在获取行情…</p>}
      {quote.isError && <p className="error" role="alert">{errorText(quote.error)}{data ? ' 下方保留上次成功获取的行情。' : ''}</p>}
      {data && (
        <div className={`quote-grid${isIndex ? ' index-quote' : ''}`}>
          <div>
            <div className="quote-heading"><h3>{data.name}</h3><span>{data.ticker}</span></div>
            <div className={`quote-price ${direction}`}>
              <strong>{number(data.last)}</strong><span>{isIndex ? '点' : '元'}</span>
              <span>{(data.change ?? 0) > 0 ? '+' : ''}{number(data.change)} ({(data.change_percent ?? 0) > 0 ? '+' : ''}{number(data.change_percent)}%)</span>
            </div>
            <p className="quote-time">
              {sourceNames[data.source]} · <time dateTime={data.as_of}>{timestampFormat.format(new Date(data.as_of))} 北京时间</time>
            </p>
            <dl className="quote-stats">
              <div><dt>今开</dt><dd>{number(data.open)}</dd></div>
              <div><dt>昨收</dt><dd>{number(data.previous_close)}</dd></div>
              <div><dt>最高／最低</dt><dd>{number(data.high)} / {number(data.low)}</dd></div>
              <div><dt>成交量（股）</dt><dd>{compact(data.volume)}</dd></div>
              <div><dt>成交额（元）</dt><dd>{compact(data.amount)}</dd></div>
              {!isIndex && <>
                <div><dt>换手率</dt><dd>{data.turnover_rate === null ? '--' : `${number(data.turnover_rate)}%`}</dd></div>
                <div><dt>市盈率</dt><dd>{number(data.pe_ratio)}</dd></div>
                <div><dt>市净率</dt><dd>{number(data.pb_ratio)}</dd></div>
                <div><dt>总市值（元）</dt><dd>{compact(data.market_cap)}</dd></div>
                <div><dt>流通市值（元）</dt><dd>{compact(data.float_market_cap)}</dd></div>
              </>}
            </dl>
            {data.warnings.map((warning) => <p key={warning} className="warnings">{messageText(warning)}</p>)}
          </div>
          {!isIndex && <div className="order-book">
            <table>
              <caption>五档盘口（股）</caption>
              <thead><tr><th>买量</th><th>买价</th><th>档位</th><th>卖价</th><th>卖量</th></tr></thead>
              <tbody>{data.bids.map((bid, index) => (
                <tr key={index}>
                  <td>{number(bid.volume, 0)}</td><td className="price-up">{number(bid.price)}</td>
                  <th scope="row">{index + 1}</th>
                  <td className="price-down">{number(data.asks[index].price)}</td><td>{number(data.asks[index].volume, 0)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>}
        </div>
      )}
    </section>
  )
}