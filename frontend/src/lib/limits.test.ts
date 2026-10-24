import { describe, expect, it } from 'vitest'
import type { LimitWindow } from './api'
import { limitLine, pressing, resetTime, tokenCount } from './limits'

const at = (hour: number, minute = 0, days = 0) => new Date(2026, 9, 24 + days, hour, minute).getTime() / 1000
const now = new Date(2026, 9, 24, 9, 0).getTime()
const window = (id: string, used: number, resets_at: number | null = null): LimitWindow =>
  ({ id, title: id === 'session' ? '5-hour' : 'weekly', used, resets_at })

describe('limits', () => {
  it('says when a limit resets', () => {
    expect(resetTime(at(16), now)).toBe('4 pm')
    expect(resetTime(at(16, 26), now)).toBe('4:26 pm')
    expect(resetTime(at(0, 5), now)).toBe('12:05 am')
    expect(resetTime(at(9, 30, 1), now)).toBe('tomorrow 9:30 am')
    expect(resetTime(at(9, 0, 3), now)).toBe('Tue 9 am')
    expect(resetTime(at(9, 0, 10), now)).toBe('3 Nov')
  })

  it('picks the window most in the way', () => {
    expect(pressing({ windows: [window('session', 40), window('weekly', 70)], checked_at: 0 })).toBeNull()
    expect(pressing({ windows: [window('session', 85), window('weekly', 92)], checked_at: 0 })?.id).toBe('weekly')
    expect(pressing({ windows: [window('session', 100, at(16)), window('weekly', 100, at(9, 0, 2))], checked_at: 0 })?.id).toBe('weekly')
    expect(pressing(undefined)).toBeNull()
  })

  it('puts a window in a line', () => {
    expect(limitLine('Claude Code', window('session', 100, at(16)), now)).toBe('Claude Code: 5-hour limit reached · resets 4 pm')
    expect(limitLine('Codex', window('weekly', 85), now)).toBe('Codex: 85% of the weekly limit used')
  })

  it('shortens token counts', () => {
    expect([950, 1000, 12_400, 12_600, 3_100_000].map(tokenCount)).toEqual(['950', '1k', '12k', '13k', '3.1M'])
    expect(tokenCount(1250)).toBe('1.3k')
  })
})
