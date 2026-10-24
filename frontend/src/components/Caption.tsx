import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

const HOLD_MS = 4000 // after you scroll the caption yourself, it stops following for this long

/**
 * Live words in Strata's display type. Committed words are solid and provisional ones light;
 * the agent speaks in italic. While the agent talks, `committed` is what has been spoken so far
 * and `provisional` the rest of the sentence. Words are keyed by position, so a word that
 * becomes committed only brightens, and each new word blurs in once.
 *
 * The caption scrolls within a few lines and glides to follow the words: the agent's last
 * spoken word sits one line above the bottom edge, so the next line shows what's coming; your
 * own words follow the newest one. Scrolling it yourself holds the view for a moment. A `still`
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
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 1
    setEdges((e) => (e.top === top && e.bottom === bottom ? e : { top, bottom }))
  }

  const total = solid.length + light.length
  const followed = agent ? solid.length - 1 : total - 1
  useEffect(() => {
    const el = box.current
    if (!el) return
    measure()
    if (still || Date.now() < heldUntil.current) return
    const word = el.querySelectorAll<HTMLElement>('[data-word]')[followed]
    if (!word) return
    const lead = agent ? parseFloat(getComputedStyle(el).lineHeight) : 0
    const top = Math.max(0, Math.min(word.offsetTop + word.offsetHeight + lead - el.clientHeight, el.scrollHeight - el.clientHeight))
    if (Math.abs(top - el.scrollTop) > 2) el.scrollTo({ top, behavior: 'smooth' })
  }, [followed, total, agent, still])

  const hold = () => { heldUntil.current = Date.now() + HOLD_MS }
  const fade = (edge: boolean) => (edge ? 'transparent' : '#000')
  const mask: CSSProperties | undefined = edges.top || edges.bottom
    ? { maskImage: `linear-gradient(${fade(edges.top)}, #000 1lh, #000 calc(100% - 1lh), ${fade(edges.bottom)})` }
    : undefined

  return (
    <div ref={box} onScroll={measure} onWheel={hold} onTouchMove={hold} style={mask}
      className={cn('relative overflow-y-auto overscroll-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
        agent ? 'max-h-[5lh] text-[clamp(19px,4.5vw,23px)] leading-[1.4]' : 'max-h-[4lh] text-[clamp(28px,7vw,40px)] leading-[1.12]')}>
      <p data-speaker={speaker} className={cn('font-serif', agent ? 'italic' : 'tracking-[-0.015em] text-balance', className)}>
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
