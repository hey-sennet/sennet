import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { voice, type Target } from './voice'

const TAP_MS = 300       // a press shorter than this latches: tap to start, tap again to send
const SLIDE_OFF_PX = 48  // releasing this far outside the control cancels the turn

export function useVoice() {
  const v = voice()
  return useSyncExternalStore(v.subscribe, v.getSnapshot)
}

/**
 * Push to talk on any button: hold, or tap to start and tap again to send. Sliding off before
 * letting go cancels. Space or Enter work the same way while it has focus. Returns the props
 * to spread on the button and its label.
 */
export function usePushToTalk(target: Target) {
  const state = useVoice()
  const pressedAt = useRef(0)
  const latched = useRef(false)
  const [tapped, setTapped] = useState(false) // `latched`, for rendering
  const ready = state.connected && state.stt === 'ready'

  const latch = (on: boolean) => {
    latched.current = on
    setTapped(on)
  }

  // A turn that ended on the server's side (an error) ends the latch too.
  useEffect(() => {
    if (!state.listening) latched.current = false
  }, [state.listening])

  const down = () => {
    if (latched.current) {
      latch(false)
      voice().release()
      return
    }
    latch(false)
    pressedAt.current = performance.now()
    void voice().press(target)
  }

  const up = (slidOff: boolean) => {
    if (latched.current || !voice().getSnapshot().listening) return
    if (slidOff) voice().cancel()
    else if (performance.now() - pressedAt.current < TAP_MS) latch(true)
    else voice().release()
  }

  const outside = (event: React.PointerEvent<HTMLElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    return event.clientX < box.left - SLIDE_OFF_PX || event.clientX > box.right + SLIDE_OFF_PX
      || event.clientY < box.top - SLIDE_OFF_PX || event.clientY > box.bottom + SLIDE_OFF_PX
  }

  const label = !state.connected ? 'Voice is connecting'
    : state.stt !== 'ready' ? 'Speech recognition is not ready; run agent-talk setup'
    : state.listening && state.opening ? 'Starting the microphone'
    : state.listening ? (tapped ? 'Listening: tap to send' : 'Listening: release to send')
    : 'Hold to talk'

  const props = {
    type: 'button' as const,
    'aria-pressed': state.listening,
    'aria-label': label,
    title: label,
    disabled: !ready && !state.listening,
    onPointerDown: (event: React.PointerEvent<HTMLElement>) => {
      if (event.button !== 0) return
      event.currentTarget.setPointerCapture(event.pointerId)
      down()
    },
    onPointerUp: (event: React.PointerEvent<HTMLElement>) => up(outside(event)),
    onPointerCancel: () => voice().cancel(),
    onKeyDown: (event: React.KeyboardEvent) => {
      if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
        event.preventDefault()
        down()
      }
    },
    onKeyUp: (event: React.KeyboardEvent) => {
      if (event.key === ' ' || event.key === 'Enter') up(false)
    },
    onContextMenu: (event: React.MouseEvent) => event.preventDefault(),
  }
  return { props, label, ready, tapped, state }
}
