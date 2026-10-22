import { useEffect, useLayoutEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { createStrata, type StrataController, type StrataSources } from './engine'
import type { StrataState } from './field'

/**
 * The Strata disc. It animates on its own loop; changing `state` or `sources` only hands the
 * new value to that loop, so audio never causes a React render. `frozenAt` draws one still
 * moment instead, for screenshots and tests.
 */
export function Strata({ state, sources, frozenAt, className }: {
  state: StrataState
  sources?: StrataSources
  frozenAt?: number
  className?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const controller = useRef<StrataController>(null)
  // The newest props, for a controller created after they changed (a new `frozenAt`).
  const latest = useRef({ state, sources })
  useLayoutEffect(() => {
    latest.current = { state, sources }
  })

  useEffect(() => {
    const strata = createStrata(canvas.current!, { state: latest.current.state, frozenAt })
    strata.setSources(latest.current.sources ?? {})
    controller.current = strata
    return () => strata.destroy()
  }, [frozenAt])

  useEffect(() => controller.current?.setState(state), [state])
  useEffect(() => controller.current?.setSources(sources ?? {}), [sources])

  return <canvas ref={canvas} aria-hidden data-state={state} className={cn('aspect-square', className)} />
}
