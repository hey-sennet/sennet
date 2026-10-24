import type { AgentLimits } from '@/lib/api'
import { resetTime, WARN_AT } from '@/lib/limits'
import { cn } from '@/lib/utils'
import { relativeTime } from './TaskList'

/** An agent's usage limits, in its row in Settings: how much of each window is used, and when it resets. */
export function Limits({ limits }: { limits: AgentLimits }) {
  return (
    <div aria-label="Usage limits" role="group" className="flex flex-col gap-2">
      {limits.windows.map((window) => (
        <div key={window.id} className="text-xs text-muted-foreground">
          <div className="flex items-baseline justify-between gap-2">
            <span className="first-letter:uppercase">{window.title} limit</span>
            <span className={cn('tabular-nums', window.used >= 100 ? 'text-destructive' : window.used >= WARN_AT && 'text-warn')}>
              {window.used >= 100 ? 'Reached' : `${window.used}% used`}
              {window.resets_at ? ` · resets ${resetTime(window.resets_at)}` : ''}
            </span>
          </div>
          <div className="mt-1 h-[3px] overflow-hidden rounded-full bg-foreground/10" role="meter" aria-valuenow={window.used}
            aria-valuemin={0} aria-valuemax={100} aria-label={`${window.title} limit used`}>
            <div className={cn('h-full rounded-full', window.used >= 100 ? 'bg-destructive' : window.used >= WARN_AT ? 'bg-warn' : 'bg-foreground/45')}
              style={{ width: `${window.used}%` }} />
          </div>
        </div>
      ))}
      <p className="text-[11px] text-muted-foreground/80">As of {relativeTime(limits.checked_at)}</p>
    </div>
  )
}
