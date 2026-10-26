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
    // After a short lead an agent wrote first despite its instructions: the lead stays.
    expect(withoutSpoken('Both work.\n\n<spoken>\nCommitted.\n</spoken>\n\n**Done.**')).toBe('Both work.\n\n**Done.**')
    expect(withoutSpoken('Both work.\n\n<spoken>Commit')).toBe('Both work.\n\n')
    expect(withoutSpoken('Both work.\n<spo')).toBe('Both work.\n')
    // Not after a long lead or inside code.
    expect(withoutSpoken('1\n2\n3\n4\n<spoken>Hi</spoken>')).toBe('1\n2\n3\n4\n<spoken>Hi</spoken>')
    expect(withoutSpoken('```\n<spoken>Hi</spoken>\n```')).toBe('```\n<spoken>Hi</spoken>\n```')
  })
})

describe('spokenPart', () => {
  it('is the finished spoken version, if there is one', () => {
    expect(spokenPart('<spoken>All 12\n pass.</spoken>\n\n**Done.** All 12 pass.')).toBe('All 12 pass.')
    expect(spokenPart('<spoken>All 12 pa')).toBeNull()
    expect(spokenPart('<spoken></spoken>Done.')).toBeNull()
    expect(spokenPart('It goes inside <spoken>x</spoken> tags.')).toBeNull()
    expect(spokenPart('Both work.\n\n<spoken>Committed\n and pushed.</spoken>\nMore.')).toBe('Committed and pushed.')
  })
})
