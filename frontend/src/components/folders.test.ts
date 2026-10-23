import { describe, expect, it } from 'vitest'
import { breadcrumbs } from './FolderBrowser'

describe('breadcrumbs', () => {
  it('start at home inside it', () => {
    expect(breadcrumbs('/home/ada/code/app', '/home/ada')).toEqual([
      { label: 'Home', path: '/home/ada' }, { label: 'code', path: '/home/ada/code' },
      { label: 'app', path: '/home/ada/code/app' }])
  })

  it('start at the root outside it, and home alone is one crumb', () => {
    expect(breadcrumbs('/srv/repo', '/home/ada').map((c) => c.path)).toEqual(['/', '/srv', '/srv/repo'])
    expect(breadcrumbs('/home/ada', '/home/ada')).toEqual([{ label: 'Home', path: '/home/ada' }])
    expect(breadcrumbs('/home/adam', '/home/ada')[0].label).toBe('/')
  })
})
