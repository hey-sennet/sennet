// `?strata=listening` shows a Strata state driven by a synthetic voice, and `&t=3.3` freezes it
// at that moment, for screenshots and tests until M2 supplies real audio.
import type { StrataSources } from './engine'
import { STATES, type StrataState } from './field'
import { syntheticVoice } from './synthetic'

const params = new URLSearchParams(location.search)
const state = params.get('strata') as StrataState | null
const t = params.get('t')

export const strataPreview = state && STATES.includes(state)
  ? {
    state,
    frozenAt: t === null ? undefined : Number(t),
    sources: { user: syntheticVoice('user'), agent: syntheticVoice('agent') } satisfies StrataSources,
  }
  : null
