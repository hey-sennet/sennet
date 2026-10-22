import { describe, expect, it } from 'vitest'
import { plainText, withoutSpoken } from './Markdown'

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
