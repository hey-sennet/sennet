import { describe, expect, it } from 'vitest'
import { plainText, spokenPart, withoutSpoken } from './Markdown'

describe('withoutSpoken', () => {
  it('drops the spoken version, finished or still streaming', () => {
    expect(withoutSpoken('<spoken>All 12 pass.</spoken>\n\n**Done.** All 12 pass.')).toBe('**Done.** All 12 pass.')
    expect(withoutSpoken('<spoken>All 12 pa')).toBe('')
    expect(withoutSpoken('<spo')).toBe('')
    expect(withoutSpoken('< 5 items')).toBe('< 5 items')
    // Talking about the tags is not a spoken version.
    expect(withoutSpoken('It goes inside `<spoken>` tags.')).toBe('It goes inside `<spoken>` tags.')
    expect(plainText('<spoken>Heard.</spoken>\n# Done\nRead.')).toBe('Done Read.')
  })
})

describe('spokenPart', () => {
  it('is the finished spoken version, if there is one', () => {
    expect(spokenPart('<spoken>All 12\n pass.</spoken>\n\n**Done.** All 12 pass.')).toBe('All 12 pass.')
    expect(spokenPart('<spoken>All 12 pa')).toBeNull()
    expect(spokenPart('<spoken></spoken>Done.')).toBeNull()
    expect(spokenPart('It goes inside <spoken>x</spoken> tags.')).toBeNull()
  })
})
