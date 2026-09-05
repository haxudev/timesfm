import { describe, expect, it } from 'vitest'
import { messageText, sourceNames } from '../i18n'

describe('display branding', () => {
  it('uses source aliases without changing transport identifiers', () => {
    expect(sourceNames.tencent).toBe('TX')
    expect(sourceNames.sina).toBe('XL')
    expect(sourceNames['akshare/tencent']).toBe('TX历史日线')
  })

  it.each([
    ['Tencent unavailable; using Sina without valuations.', 'TX行情不可用，已切换XL；估值数据暂缺。'],
    ['腾讯行情不可用，已切换新浪。', 'TX行情不可用，已切换XL。'],
    ['TimesFM 3.0 预训练模型暂不可用。', '时序模型 预训练模型暂不可用。'],
    ['TimesFM 3 最多使用 15,360 条上下文，本次已截断超出部分。', '时序模型 最多使用 15,360 条上下文，本次已截断超出部分。'],
    ['TimesFM 3 truncated each context to 15,360 values.', '时序模型 已将每个上下文截断至 15,360 条。'],
  ])('normalizes upstream display text: %s', (message, expected) => {
    expect(messageText(message)).toBe(expected)
  })
})