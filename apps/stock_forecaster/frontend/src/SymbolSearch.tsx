import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useId, useState } from 'react'
import { api } from './api'
import type { ResearchSymbol } from './types'

export function SymbolSearch({ selected, onSelect }: { selected: ResearchSymbol; onSelect: (symbol: ResearchSymbol) => void }) {
  const [input, setInput] = useState(selected.ticker)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const listId = useId()
  const search = useQuery({
    queryKey: ['symbols', input.trim()],
    queryFn: ({ signal }) => api.symbols(input.trim(), signal),
    enabled: open && input.trim().length > 0,
    retry: false,
    staleTime: 60_000,
  })
  const items = search.data?.items ?? []
  const choose = (symbol: ResearchSymbol) => {
    setInput(symbol.ticker)
    setOpen(false)
    setActive(-1)
    onSelect(symbol)
  }
  return (
    <div className="symbol-search" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false) }}>
      <label htmlFor={`${listId}-input`}>搜索股票</label>
      <div className="search-field">
        <Search size={18} aria-hidden="true" />
        <input id={`${listId}-input`} role="combobox" autoComplete="off" value={input} maxLength={100}
          aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
          aria-activedescendant={open && active >= 0 && items[active] ? `${listId}-${active}` : undefined}
          placeholder="股票名称或代码"
          onFocus={() => setOpen(true)}
          onChange={(event) => { setInput(event.target.value); setActive(-1); setOpen(true) }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') { setOpen(false); return }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault()
              setOpen(true)
              setActive((current) => items.length ? (current + (event.key === 'ArrowDown' ? 1 : items.length - 1) + items.length) % items.length : -1)
            }
            if (event.key === 'Enter') {
              event.preventDefault()
              if (open && items[active >= 0 ? active : 0]) choose(items[active >= 0 ? active : 0])
            }
          }} />
      </div>
      {open && <div className="search-results">
        <ul id={listId} role="listbox" aria-label="搜索结果">
          {items.map((symbol, index) => <li key={symbol.ticker} id={`${listId}-${index}`} role="option"
            aria-selected={active === index} onMouseDown={(event) => event.preventDefault()}
            onClick={() => choose(symbol)}>
            <span>{symbol.name ?? symbol.ticker}</span><small>{symbol.ticker} · {symbol.instrument_type === 'index' ? '指数' : '股票'}</small>
          </li>)}
        </ul>
        {search.isFetching && <p role="status">正在搜索…</p>}
        {search.isError && <p role="alert">证券搜索暂不可用，请稍后重试。</p>}
        {!search.isFetching && !search.isError && items.length === 0 && <p role="status">未找到证券</p>}
      </div>}
    </div>
  )
}