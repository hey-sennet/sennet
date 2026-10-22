import { cn } from '@/lib/utils'

/**
 * Live words in Strata's display type. Committed words are solid and provisional ones light;
 * the agent speaks in italic. While the agent talks, `committed` is what has been spoken so far
 * and `provisional` the rest of the sentence. Words are keyed by position, so a word that
 * becomes committed only brightens, and each new word blurs in once.
 */
export function Caption({ committed, provisional = '', speaker = 'user', className }: {
  committed: string
  provisional?: string
  speaker?: 'user' | 'agent'
  className?: string
}) {
  const solid = split(committed)
  const light = split(provisional)
  return (
    <p data-speaker={speaker} className={cn('font-serif text-[clamp(30px,8vw,44px)] leading-[1.02] tracking-[-0.02em] text-balance',
      speaker === 'agent' && 'italic', className)}>
      {[...solid, ...light].map((word, i) => (
        <span key={i}>
          {i > 0 && ' '}
          <span className={cn('inline-block animate-word-in transition-colors duration-350 ease-strata',
            i >= solid.length && 'text-muted-foreground')}>{word}</span>
        </span>
      ))}
    </p>
  )
}

const split = (text: string) => text.split(/\s+/).filter(Boolean)
