import { useMemo, useState } from 'react'
import { Square } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Message, type Project, type StepStatus, type TaskDetail as Detail, type TaskEvent, type TurnStatus } from '@/lib/api'
import { tokenCount } from '@/lib/limits'
import { cn } from '@/lib/utils'
import { ApprovalCard } from './ApprovalCard'
import { Diff } from './Diff'
import { Markdown } from './Markdown'
import { StatusBadge } from './StatusBadge'

type Entry = { at: number; message: Message } | { at: number; event: TaskEvent }

const VISIBLE_EVENTS = new Set(['command_finished', 'file_change', 'plan', 'error', 'turn_completed', 'background', 'turn_started'])

/**
 * A task's timeline (design/README.md): your request in a bubble, then commands with their results,
 * files edited and the agent's reply set for reading. The task's own approvals sit at the end.
 */
export function TaskDetail({ detail, streaming, project, approvals, stop = true }: {
  detail: Detail
  streaming: string
  project?: Project
  approvals: Approval[]
  stop?: boolean // false where the stop button is pinned above the timeline instead (the phone's sheet)
}) {
  const active = detail.status === 'running' || detail.status === 'awaiting_approval'

  const entries = useMemo<Entry[]>(() => {
    const messages: Entry[] = detail.messages.map((message) => ({ at: message.created_at, message }))
    // Only the latest plan matters; earlier ones are superseded.
    const lastPlan = detail.events.findLast((e) => e.kind === 'plan')
    const events: Entry[] = detail.events
      .filter((e) => VISIBLE_EVENTS.has(e.kind) && (e.kind !== 'plan' || e === lastPlan))
      .filter((e) => e.kind !== 'turn_completed' || e.data.status !== 'completed')
      .map((event) => ({ at: event.created_at, event }))
    return [...messages, ...events].sort((a, b) => a.at - b.at)
  }, [detail.messages, detail.events])

  return (
    <div>
      <p className="text-xs text-muted-foreground">
        {project?.name ?? 'Removed project'} · {detail.agent}
        {detail.input_tokens + detail.output_tokens > 0 && (
          <span title={`${detail.input_tokens.toLocaleString()} input tokens, ${detail.cached_tokens.toLocaleString()} of them from cache; ${detail.output_tokens.toLocaleString()} output`}>
            {' '}· {tokenCount(detail.input_tokens)} in, {tokenCount(detail.output_tokens)} out
          </span>
        )}
      </p>
      <h2 className="mt-1 font-serif text-[26px] leading-[1.2] tracking-[-0.01em] text-balance">{detail.title}</h2>
      <div className="mt-2.5 flex items-center justify-between gap-3">
        <StatusBadge status={detail.status} />
        {stop && <StopTask detail={detail} />}
      </div>

      <ol className="mt-7 flex flex-col gap-[18px] border-l pl-4">
        {entries.map((entry) =>
          'message' in entry ? <MessageItem key={`m${entry.message.id}`} message={entry.message} /> : <EventItem key={`e${entry.event.id}`} event={entry.event} />,
        )}
        {streaming && <li><Markdown className="font-serif text-[16px] leading-[1.6] text-muted-foreground">{streaming}</Markdown></li>}
        {active && !streaming && !approvals.length && (
          <li role="status" aria-label="Working" className="flex h-4 items-center gap-1 text-agent">
            {/* The stage's rising dots, small. */}
            {[0, 1, 2].map((i) => (
              <span key={i} className="animate-dot size-1 rounded-full bg-current" style={{ animationDelay: `${i * 0.16}s` }} />
            ))}
          </li>
        )}
        {approvals.map((approval) => <li key={approval.id}><ApprovalCard approval={approval} project={project} /></li>)}
        {detail.error && <li className="text-sm text-destructive">{detail.error}</li>}
      </ol>
    </div>
  )
}

/** Stops a task that is working, queued or in the background; nothing for a finished one. */
export function StopTask({ detail, className }: { detail: Detail; className?: string }) {
  if (!['running', 'awaiting_approval', 'queued', 'background'].includes(detail.status)) return null
  const cancel = async () => {
    try {
      await api.cancel(detail.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not cancel')
    }
  }
  return (
    <Button variant="ghost" size="sm" className={cn('text-muted-foreground', className)} onClick={cancel}>
      <Square className="fill-current" /> Stop task
    </Button>
  )
}

/** Your messages sit in a bubble on the right, like a chat; the agent's replies run as prose. */
function MessageItem({ message }: { message: Message }) {
  const faded = cn(message.state === 'queued' && 'opacity-60', message.state === 'cancelled' && 'line-through opacity-40')
  if (message.role === 'agent') {
    return <li className={faded}><Markdown className="font-serif text-[16px] leading-[1.6]">{message.text}</Markdown></li>
  }
  return (
    <li className={cn('mt-2 ml-10 self-end first:mt-0', faded)}>
      <p className="rounded-2xl rounded-br-md bg-secondary px-3.5 py-2 font-serif text-[16px] leading-[1.5] whitespace-pre-wrap [overflow-wrap:anywhere]">
        {message.text}
      </p>
      {message.state === 'queued' && <span className="mt-1 block text-right text-xs text-muted-foreground">Queued</span>}
    </li>
  )
}

function EventItem({ event }: { event: TaskEvent }) {
  const { kind, data } = event
  if (kind === 'command_finished') {
    const failed = data.status !== 'completed' || (data.exit_code ?? 0) !== 0
    const result = data.status === 'declined' ? 'declined' : failed ? `exit ${data.exit_code ?? '?'}` : 'done'
    return (
      <li>
        <details className="group text-[13px]">
          <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <code className="font-mono text-[12.5px] break-all">› {data.command}</code>
            <small className={cn('text-xs', failed ? 'text-warn' : 'text-muted-foreground')}>{result}</small>
          </summary>
          {data.output_tail && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed">{data.output_tail}</pre>
          )}
        </details>
      </li>
    )
  }
  if (kind === 'file_change') return <FileChangeEntry data={data} />
  if (kind === 'plan') {
    return (
      <li>
        <ol className="text-[13px]">
          {data.steps.map((step: { step: string; status: StepStatus }, i: number) => (
            <li key={i} className={cn('flex gap-2 py-0.5', step.status === 'done' && 'text-muted-foreground line-through')}>
              <span className="text-muted-foreground">{step.status === 'active' ? '→' : step.status === 'done' ? '✓' : '·'}</span>
              {step.step}
            </li>
          ))}
        </ol>
      </li>
    )
  }
  if (kind === 'background') {
    const work = (data.tasks as { id: string; description: string }[]).map((task) => task.description)
    return <li className="text-xs text-muted-foreground">{work.length ? `Still running: ${work.join('; ')}` : 'Background work finished'}</li>
  }
  if (kind === 'turn_started') {
    return data.by === 'agent' ? <li className="text-xs text-muted-foreground">The agent picked this up again</li> : null
  }
  if (kind === 'error') return <li className="text-xs text-warn">{data.message}{data.will_retry ? ' (retrying)' : ''}</li>
  if (kind === 'turn_completed') {
    const status = data.status as TurnStatus
    const said = status === 'cancelled' ? 'Stopped' : status === 'failed' ? "This didn't finish" : 'Ended'
    // At a usage limit the error is Sennet's own sentence, saying which and when it resets.
    if (data.limit && data.error) return <li className="text-xs text-warn">{data.error}</li>
    return <li className={cn('text-xs', status === 'failed' ? 'text-warn' : 'text-muted-foreground')}>{said}{data.error ? `: ${data.error}` : ''}</li>
  }
  return null
}

/** Files the agent edited; each one with a diff opens to show it. */
function FileChangeEntry({ data }: { data: TaskEvent['data'] }) {
  const [open, setOpen] = useState<string | null>(null)
  const verb = data.status === 'completed' ? 'edited' : data.status === 'declined' ? 'declined'
    : data.status === 'failed' ? 'failed to edit' : 'editing'
  const changes: { path: string; diff?: string }[] = data.changes
  const shown = changes.find((c) => c.path === open)
  return (
    <li className="text-[13px]">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <small className={cn('text-xs', data.status === 'failed' ? 'text-warn' : 'text-muted-foreground')}>{verb}</small>
        {changes.map((change) => change.diff ? (
          <button key={change.path} type="button" aria-expanded={open === change.path} title={change.path}
            className={cn('font-mono text-[12.5px] break-all underline decoration-dotted underline-offset-4 hover:text-foreground',
              open === change.path ? 'text-foreground' : 'text-foreground/85')}
            onClick={() => setOpen(open === change.path ? null : change.path)}>
            {change.path.split('/').pop()}
          </button>
        ) : (
          <span key={change.path} title={change.path} className="font-mono text-[12.5px] break-all">{change.path.split('/').pop()}</span>
        ))}
      </div>
      {shown?.diff && <Diff diff={shown.diff} className="mt-2" />}
    </li>
  )
}
