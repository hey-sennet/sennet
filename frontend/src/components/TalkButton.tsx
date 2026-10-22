import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Mic } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { voice, type Target } from '@/voice/voice'

const TAP_MS = 300       // a press shorter than this latches: tap to start, tap again to send
const SLIDE_OFF_PX = 48  // releasing this far outside the button cancels the turn

export function useVoice() {
  const v = voice()
  return useSyncExternalStore(v.subscribe, v.getSnapshot)
}

/**
 * Push to talk: hold, or tap to start and tap again to send. Sliding off the button before
 * letting go cancels. Space or Enter work the same way while it has focus.
 */
export function TalkButton({ target, className }: { target: Target; className?: string }) {
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

  const outside = (event: React.PointerEvent<HTMLButtonElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    return event.clientX < box.left - SLIDE_OFF_PX || event.clientX > box.right + SLIDE_OFF_PX
      || event.clientY < box.top - SLIDE_OFF_PX || event.clientY > box.bottom + SLIDE_OFF_PX
  }

  const label = !state.connected ? 'Voice is connecting'
    : state.stt !== 'ready' ? 'Speech recognition is not ready; run agent-talk setup'
    : state.listening && state.opening ? 'Starting the microphone'
    : state.listening ? (tapped ? 'Listening: tap to send' : 'Listening: release to send')
    : 'Hold to talk'

  return (
    <Button
      type="button"
      variant={state.listening ? 'default' : 'outline'}
      aria-pressed={state.listening}
      aria-label={label}
      title={label}
      disabled={!ready && !state.listening}
      className={cn('h-12 touch-none gap-2 rounded-full px-5 select-none', className)}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        event.currentTarget.setPointerCapture(event.pointerId)
        down()
      }}
      onPointerUp={(event) => up(outside(event))}
      onPointerCancel={() => voice().cancel()}
      onKeyDown={(event) => {
        if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
          event.preventDefault()
          down()
        }
      }}
      onKeyUp={(event) => {
        if (event.key === ' ' || event.key === 'Enter') up(false)
      }}
      onContextMenu={(event) => event.preventDefault()}
    >
      <Mic className={cn(state.listening && 'animate-pulse')} />
      {state.listening ? (state.opening ? 'Starting…' : 'Listening') : 'Talk'}
    </Button>
  )
}
