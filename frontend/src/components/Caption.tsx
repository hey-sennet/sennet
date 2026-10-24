import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

const HOLD_MS = 4000 // after you scroll the caption yourself, it stops following for this long
const PACE = 20 // characters a second the voice speaks, until the caption has timed it
const PACE_RANGE = [10, 32]
const SETTLE_MS = 450 // a long sentence's first line eases into place over this
const CHASE_MS = 140 // your words' caption closes most of the way to a new line in about twice this

/**
 * Live words in Strata's display type. Committed words are solid and provisional ones light;
 * the agent speaks in italic. While the agent talks, `committed` is what has been spoken so far
 * and `provisional` the rest of the sentence. Words are keyed by position, so a word that
 * becomes committed only brightens, and each new word blurs in once.
 *
 * The caption scrolls within a few lines and glides to follow the words: the agent's last
 * spoken word sits one line above the bottom edge, so the next line shows what's coming; your
 * own words follow the newest one. A sentence too tall for the caption starts at its first line
 * and glides through to its end as it is spoken. Scrolling it yourself holds the view for a moment. A `still`
 * caption doesn't follow: it stays where it was left, to be scrolled and read.
 */
export function Caption({ committed, provisional = '', speaker = 'user', still, className }: {
  committed: string
  provisional?: string
  speaker?: 'user' | 'agent'
  still?: boolean
  className?: string
}) {
  const solid = split(committed)
  const light = split(provisional)
  const agent = speaker === 'agent'
  const box = useRef<HTMLDivElement>(null)
  const heldUntil = useRef(0)
  const [edges, setEdges] = useState({ top: false, bottom: false })

  const measure = () => {
    const el = box.current
    if (!el) return
    const top = el.scrollTop > 1
    const bottom = el.scrollTop + el.clientHeight < textHeight(el) - 1
    setEdges((e) => (e.top === top && e.bottom === bottom ? e : { top, bottom }))
  }

  const total = solid.length + light.length
  const followed = agent ? solid.length - 1 : total - 1
  // The agent's words arrive a sentence at a time, as each starts playing: `sentence` is where
  // the one playing now begins, and `last` when the one before it began, to time the voice by.
  const sentence = useRef({ from: 0, to: 0, said: '' })
  const last = useRef<{ at: number; chars: number } | null>(null)
  const pace = useRef(PACE)
  const glide = useRef(0)
  // Your words are re-guessed a few times a second, so their tail grows and shrinks across a line:
  // while you talk the caption only moves down, to `reach`, chasing it in one continuous glide, and
  // the words keep the height they reached, so the browser never pulls the view up to fit them.
  const reach = useRef(0)
  const floor = useRef(0) // the words' tallest yet in this turn
  useEffect(() => () => cancelAnimationFrame(glide.current), [])
  useEffect(() => {
    const el = box.current
    if (!el) return
    measure()
    const text = el.firstElementChild as HTMLElement
    if (agent || still || total === 0) { // not your words being heard: no floor
      reach.current = floor.current = 0
      text.style.minHeight = ''
    }
    if (still) return
    const words = el.querySelectorAll<HTMLElement>('[data-word]')
    const word = words[followed]
    if (!word) return
    const line = parseFloat(getComputedStyle(el).lineHeight)
    const max = Math.max(textHeight(el) - el.clientHeight, 0)
    const end = clamp(word.offsetTop + word.offsetHeight + (agent ? line : 0) - el.clientHeight, 0, max)
    const said = solid.join(' ')
    const started = agent && said !== sentence.current.said
    if (started) {
      const now = performance.now()
      const more = sentence.current.said !== '' && said.startsWith(sentence.current.said + ' ')
      const from = more ? sentence.current.to : 0
      if (!more) last.current = null // a new reply
      else if (last.current) learn(pace, last.current.chars / ((now - last.current.at) / 1000))
      sentence.current = { from, to: solid.length, said }
      last.current = { at: now, chars: solid.slice(from).join(' ').length }
    }
    if (Date.now() < heldUntil.current) return
    if (!agent) {
      // The words never get shorter while you talk, so a shrinking tail can't pull the view up.
      floor.current = Math.max(floor.current, word.offsetTop + word.offsetHeight)
      text.style.minHeight = `${floor.current}px`
      reach.current = Math.min(Math.max(reach.current, end), floor.current - el.clientHeight)
      cancelAnimationFrame(glide.current) // the chase keeps no state, so taking it up again is seamless
      chase(el, reach, heldUntil, glide)
      return
    }
    // A sentence taller than the caption starts at its first line, with a line of what came
    // before, then glides down through it at the voice's pace.
    const first = words[sentence.current.from]
    const start = agent && first ? clamp(first.offsetTop - line, 0, max) : end
    if (start < end) {
      if (started) {
        cancelAnimationFrame(glide.current)
        follow(el, start, end, (last.current!.chars / pace.current) * 1000, heldUntil, glide)
      }
      return // still gliding through it
    }
    cancelAnimationFrame(glide.current)
    if (Math.abs(end - el.scrollTop) > 2) el.scrollTo({ top: end, behavior: 'smooth' })
  }, [followed, total, agent, still])

  const hold = () => {
    heldUntil.current = Date.now() + HOLD_MS
    cancelAnimationFrame(glide.current)
  }
  const fade = (edge: boolean) => (edge ? 'transparent' : '#000')
  const mask: CSSProperties | undefined = edges.top || edges.bottom
    ? { maskImage: `linear-gradient(${fade(edges.top)}, #000 1lh, #000 calc(100% - 1lh), ${fade(edges.bottom)})` }
    : undefined

  return (
    <div ref={box} onScroll={measure} onWheel={hold} onTouchMove={hold} style={mask}
      className={cn('relative overflow-y-auto overscroll-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
        agent ? 'max-h-[5lh] text-[clamp(19px,4.5vw,23px)] leading-[1.4]' : 'max-h-[5lh] text-[clamp(22px,5.5vw,30px)] leading-[1.18]')}>
      <p data-speaker={speaker} className={cn('font-serif', agent ? 'italic' : 'tracking-[-0.015em]', className)}>
        {[...solid, ...light].map((word, i) => (
          <span key={i}>
            {i > 0 && ' '}
            <span data-word className={cn('inline-block max-w-full animate-word-in transition-colors duration-350 ease-strata [overflow-wrap:anywhere]',
              i >= solid.length && 'text-muted-foreground')}>{word}</span>
          </span>
        ))}
      </p>
    </div>
  )
}

const split = (text: string) => text.split(/\s+/).filter(Boolean)

/** The words' laid-out height. Not `scrollHeight`: a word blurring in starts a little low, and
 * that would count as more to scroll, fading the last line until the next word. */
const textHeight = (el: HTMLElement) => (el.firstElementChild as HTMLElement | null)?.offsetHeight ?? el.scrollHeight
const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high)

/** Learns the voice's pace from a sentence's characters per second of playing. A wait for the
 * agent to write the next sentence isn't speech, so a pace far off the voice's is ignored. */
function learn(pace: { current: number }, measured: number) {
  if (measured >= PACE_RANGE[0] && measured <= PACE_RANGE[1]) pace.current += (measured - pace.current) * 0.3
}

/** Eases to `from`, then moves to `to` in step with the voice, ending as the sentence does.
 * Scrolling it yourself (`heldUntil`) lets go of it; `frame` is the animation frame to cancel. */
function follow(el: HTMLElement, from: number, to: number, ms: number, heldUntil: { current: number },
  frame: { current: number }) {
  const begin = el.scrollTop
  const t0 = performance.now()
  const step = (t: number) => {
    if (Date.now() < heldUntil.current) return
    const elapsed = t - t0
    el.scrollTop = elapsed < SETTLE_MS
      ? begin + (from - begin) * ease(elapsed / SETTLE_MS)
      : from + (to - from) * Math.min(1, (elapsed - SETTLE_MS) / Math.max(ms - SETTLE_MS, 1))
    if (elapsed < ms) frame.current = requestAnimationFrame(step)
  }
  frame.current = requestAnimationFrame(step)
}

/** Eases towards `target` until it gets there, following it as it moves without starting again.
 * Scrolling it yourself (`heldUntil`) lets go of it; `frame` is the animation frame to cancel. */
function chase(el: HTMLElement, target: { current: number }, heldUntil: { current: number }, frame: { current: number }) {
  let before = performance.now()
  const step = (t: number) => {
    const gap = target.current - el.scrollTop
    if (Date.now() < heldUntil.current || Math.abs(gap) < 1) {
      if (Math.abs(gap) < 1) el.scrollTop = target.current
      frame.current = 0
      return
    }
    const move = gap * (1 - Math.exp(-(t - before) / CHASE_MS))
    el.scrollTop += Math.abs(move) < 1 ? Math.sign(gap) : move // a browser may round to whole pixels
    before = t
    frame.current = requestAnimationFrame(step)
  }
  frame.current = requestAnimationFrame(step)
}

const ease = (t: number) => 1 - (1 - t) ** 5 // near --ease-strata, cubic-bezier(0.22, 1, 0.36, 1)
