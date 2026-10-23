import { cn } from '@/lib/utils'

/** A unified diff, in Strata's colours: additions in the agent's, removals in the destructive. */
export function Diff({ diff, className }: { diff: string; className?: string }) {
  return (
    <pre className={cn('max-h-72 overflow-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed', className)}>
      {diff.split('\n').map((line, i) => (
        <span key={i} className={cn('block',
          line.startsWith('@@') ? 'text-muted-foreground'
            : line.startsWith('+') && !line.startsWith('+++') ? 'text-agent'
            : line.startsWith('-') && !line.startsWith('---') && 'text-destructive')}>
          {line || ' '}
        </span>
      ))}
    </pre>
  )
}
