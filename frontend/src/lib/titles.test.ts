import { describe, expect, it } from 'vitest'
import { repeatsTitle } from './titles'

describe('repeatsTitle', () => {
  it('matches a title made from the message, ignoring case, punctuation and fillers', () => {
    expect(repeatsTitle('look', 'look')).toBe(true)
    expect(repeatsTitle('The build fails', 'Um, okay so the build fails.')).toBe(true)
    expect(repeatsTitle('Why is the build, slow', 'why is the build, uh, slow?')).toBe(true)
  })

  it('leaves a message with more to say than its title', () => {
    expect(repeatsTitle('Add a health check', 'please add a health check')).toBe(false) // the agent's title
    expect(repeatsTitle('Fix the build', 'Fix the build\nIt fails on main since this morning')).toBe(false) // the first line only
    expect(repeatsTitle('what changed in the last release and is the change…', 'what changed in the last release and is the changelog right')).toBe(false)
    expect(repeatsTitle('An image', '')).toBe(false)
  })
})
