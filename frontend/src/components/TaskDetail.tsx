import { useMemo } from 'react'
import { Square } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Message, type Project, type StepStatus, type TaskDetail as Detail, type TaskEvent, type TurnStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ApprovalCard } from './ApprovalCard'
import { Markdown } from './Markdown'
import { StatusBadge } from './StatusBadge'

type Entry = { at: number; message: Message } | { at: number; event: TaskEvent }

const VISIBLE_EVENTS = new Set(['command_finished', 'file_change', 'plan', 'error', 'turn_completed'])

/**
 * A task's timeline (design/README.md): your request in a bubble, then commands with their results,
 * files edited and the agent's reply set for reading. The task's own approvals sit at the end.
 */
export function TaskDetail({ detail, streaming, project, approvals }: {
  detail: Detail
  streaming: string
  project?: Project
  approvals: Approval[]
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

  const cancel = async () => {
    try {
      await api.cancel(detail.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not cancel')
    }
  }

  return (
    <div>
      <p className="text-xs text-muted-foreground">{project?.name ?? 'Removed project'} · {detail.agent}</p>
      <h2 className="mt-1 font-serif text-[26px] leading-[1.2] tracking-[-0.01em] text-balance">{detail.title}</h2>
      <div className="mt-2.5 flex items-center justify-between gap-3">
        <StatusBadge status={detail.status} />
        {(active || detail.status === 'queued') && (
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={cancel}>
            <Square className="fill-current" /> Stop task
          </Button>
        )}
      </div>

      <ol className="mt-7 flex flex-col gap-[18px] border-l pl-4">
        {entries.map((entry) =>
          'message' in entry ? <MessageItem key={`m${entry.message.id}`} message={entry.message} /> : <EventItem key={`e${entry.event.id}`} event={entry.event} />,
        )}
        {streaming && <li><Markdown className="font-serif text-[16px] leading-[1.6] text-muted-foreground">{streaming}</Markdown></li>}
        {active && !streaming && !approvals.length && (
          <li className="text-xs text-muted-foreground"><span className="animate-pulse">Working…</span></li>
        )}
        {approvals.map((approval) => <li key={approval.id}><ApprovalCard approval={approval} project={project} /></li>)}
        {detail.error && <li className="text-sm text-destructive">{detail.error}</li>}
      </ol>
    </div>
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
  if (kind === 'file_change') {
    const verb = data.status === 'completed' ? 'edited' : data.status === 'declined' ? 'declined'
      : data.status === 'failed' ? 'failed to edit' : 'editing'
    return (
      <li className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-[13px]">
        <small className={cn('text-xs', data.status === 'failed' ? 'text-warn' : 'text-muted-foreground')}>{verb}</small>
        <span className="font-mono text-[12.5px] break-all">{data.changes.map((c: { path: string }) => c.path.split('/').pop()).join(', ')}</span>
      </li>
    )
  }
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
  if (kind === 'error') return <li className="text-xs text-warn">{data.message}{data.will_retry ? ' (retrying)' : ''}</li>
  if (kind === 'turn_completed') {
    const status = data.status as TurnStatus
    return <li className="text-xs text-muted-foreground">{status === 'cancelled' ? 'Stopped' : `Turn ${status}`}{data.error ? `: ${data.error}` : ''}</li>
  }
  return null
}
