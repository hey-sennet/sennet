import { memo, useMemo, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { ChevronDown, Square } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogOverlay, DialogPortal, DialogTitle } from '@/components/ui/dialog'
import { useAgents } from '@/lib/agents'
import { api, attachmentUrl, thumbnailUrl, type Approval, type Attachment, type Message, type Project, type StepStatus, type TaskDetail as Detail, type TaskEvent, type TurnStatus } from '@/lib/api'
import { tokenCount } from '@/lib/limits'
import { repeatsTitle } from '@/lib/titles'
import { useStreaming, type Streaming } from '@/lib/store'
import { cn } from '@/lib/utils'
import { ApprovalCard } from './ApprovalCard'
import { Diff } from './Diff'
import { Markdown } from './Markdown'
import { StatusBadge, StatusDot } from './StatusBadge'

type Entry = { at: number; message: Message } | { at: number; key: number; event: TaskEvent } | { at: number; key: number; tools: TaskEvent[] }

const VISIBLE_EVENTS = new Set(['command_started', 'command_finished', 'tool_used', 'file_change', 'plan', 'error', 'turn_completed', 'background', 'turn_started'])

/**
 * A task's timeline (design/README.md): your request in a bubble, then commands with their results,
 * the other tools it used, files edited and the agent's reply set for reading. The task's own approvals sit at the end.
 */
export function TaskDetail({ detail, streaming, project, approvals, stop = true }: {
  detail: Detail
  streaming: Streaming
  project?: Project
  approvals: Approval[]
  stop?: boolean // false where the stop button is pinned above the timeline instead (the phone's sheet)
}) {
  const agents = useAgents()
  const active =detail.status === 'running' || detail.status === 'awaiting_approval'
  const working = active || detail.status === 'background' // a command can outlast its turn

  const entries = useMemo<Entry[]>(() => {
    const messages: Entry[] = detail.messages.map((message) => ({ at: message.created_at, message }))
    // Only the latest plan matters; earlier ones are superseded.
    const lastPlan = detail.events.findLast((e) => e.kind === 'plan')
    const events: Entry[] = []
    const running = new Map<string, Entry & { event: TaskEvent }>() // command or tool call → its row, until it finishes
    for (const event of detail.events) {
      if (!VISIBLE_EVENTS.has(event.kind) || (event.kind === 'plan' && event !== lastPlan)) continue
      if (event.kind === 'turn_completed' && event.data.status === 'completed') continue
      const started = event.kind === 'command_finished' || event.kind === 'tool_used' ? running.get(event.data.item_id) : undefined
      if (started) {
        // A result takes the place of its running row, where it started.
        started.event = event
        running.delete(event.data.item_id)
        continue
      }
      const entry = { at: event.created_at, key: event.id, event }
      if (event.kind === 'command_started' || (event.kind === 'tool_used' && event.data.status === 'running')) {
        running.set(event.data.item_id, entry)
      }
      events.push(entry)
    }
    // Tool calls in a row, with nothing said or run between them, fold into one line.
    const folded: Entry[] = []
    for (const entry of [...messages, ...events].sort((a, b) => a.at - b.at)) {
      const last = folded.at(-1)
      if (!('event' in entry) || entry.event.kind !== 'tool_used') folded.push(entry)
      else if (last && 'tools' in last) last.tools.push(entry.event)
      else folded.push({ at: entry.at, key: entry.key, tools: [entry.event] })
    }
    return folded
  }, [detail.messages, detail.events])
  // The heading already says it: a task titled from your first words doesn't show them twice.
  const first = detail.messages[0]
  const repeated = first?.role === 'user' && repeatsTitle(detail.title, first.text) ? first.id : null

  return (
    <div>
      <p className="text-xs text-muted-foreground">
        {project?.name ?? 'Removed project'} · {agents?.[detail.agent]?.title ?? detail.agent}
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
          'message' in entry ? <MessageItem key={`m${entry.message.id}`} message={entry.message} wordless={entry.message.id === repeated} />
            : 'tools' in entry ? <ToolCalls key={`t${entry.key}`} calls={entry.tools} working={working} />
            : <EventItem key={`e${entry.key}`} event={entry.event} working={working} />,
        )}
        <StreamingReply streaming={streaming} working={active && !approvals.length} />
        {approvals.map((approval) => <li key={approval.id}><ApprovalCard approval={approval} project={project} /></li>)}
        {detail.error && <li className="text-sm text-destructive">{detail.error}</li>}
      </ol>
    </div>
  )
}

/** How many pieces of background work show before the list folds to its first. */
const UNFOLDED = 2

/**
 * Work the agent left running, below the timeline rather than in it, so it stays in view
 * however far the timeline is scrolled; a long list folds to its first, and opens to scroll.
 */
export function BackgroundWork({ detail, className }: { detail: Detail; className?: string }) {
  const [open, setOpen] = useState(false)
  const work: { id: string; description: string }[] = useMemo(() =>
    ['running', 'awaiting_approval', 'background', 'queued'].includes(detail.status)
      ? detail.events.findLast((e) => e.kind === 'background')?.data.tasks ?? [] : [],
  [detail.events, detail.status])
  if (!work.length) return null
  const folds = work.length > UNFOLDED
  const shown = folds && !open ? work.slice(0, 1) : work
  const label = (
    <>
      <StatusDot status="background" />
      Running in the background
      {folds && <span className="ml-auto flex items-center gap-1 font-normal text-muted-foreground">
        {work.length}<ChevronDown className={cn('size-3.5 transition-transform duration-300 ease-strata', open && 'rotate-180')} />
      </span>}
    </>
  )
  return (
    <section aria-label="Running in the background" className={cn('rounded-2xl border px-3.5 py-2.5', className)}>
      {folds ? (
        <button type="button" aria-expanded={open} className="flex w-full items-center gap-1.5 text-xs font-medium text-agent" onClick={() => setOpen(!open)}>
          {label}
        </button>
      ) : <p className="flex items-center gap-1.5 text-xs font-medium text-agent">{label}</p>}
      <ul className={cn('mt-1.5 flex flex-col gap-1', open && 'max-h-[min(30dvh,180px)] overflow-y-auto overscroll-contain')}>
        {shown.map((task) => (
          <li key={task.id} className="flex min-w-0 items-baseline gap-2.5 font-mono text-[12.5px]">
            <span className="text-muted-foreground/60">›</span>
            <code className="min-w-0 flex-1 truncate animate-shimmer" title={task.description}>{task.description}</code>
          </li>
        ))}
        {folds && !open && (
          <li><button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(true)}>
            and {work.length - 1} more
          </button></li>
        )}
      </ul>
    </section>
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

/** The reply as it streams in, or the working dots until it starts. Only this re-renders for each
 *  delta: the rest of the timeline stays as it is (memo), its markdown not parsed again. */
function StreamingReply({ streaming, working }: { streaming: Streaming; working: boolean }) {
  const text = useStreaming(streaming)
  if (text) return <li><Markdown className="font-serif text-[16px] leading-[1.6] text-muted-foreground">{text}</Markdown></li>
  if (!working) return null
  return (
    <li role="status" aria-label="Working" className="flex h-4 items-center gap-1 text-agent">
      {/* The stage's rising dots, small. */}
      {[0, 1, 2].map((i) => (
        <span key={i} className="animate-dot size-1 rounded-full bg-current" style={{ animationDelay: `${i * 0.16}s` }} />
      ))}
    </li>
  )
}

/** Your messages sit in a bubble on the right, like a chat; the agent's replies run as prose. */
const MessageItem = memo(function MessageItem({ message, wordless }: {
  message: Message
  wordless?: boolean // its words are the task's title: only its images, if any, show
}) {
  if (wordless && !message.attachments.length) return null
  const faded = cn(message.state === 'queued' && 'opacity-60', message.state === 'cancelled' && 'line-through opacity-40')
  if (message.role === 'agent') {
    return <li className={faded}><Markdown className="font-serif text-[16px] leading-[1.6]">{message.text}</Markdown></li>
  }
  return (
    <li className={cn('mt-2 ml-10 self-end first:mt-0', faded)}>
      {message.attachments.length > 0 && <MessageImages images={message.attachments} />}
      {message.text && !wordless && (
        <p className="ml-auto w-fit rounded-2xl rounded-br-md bg-secondary px-3.5 py-2 font-serif text-[16px] leading-[1.5] whitespace-pre-wrap [overflow-wrap:anywhere]">
          {message.text}
        </p>
      )}
      {message.state === 'queued' && <span className="mt-1 block text-right text-xs text-muted-foreground">Queued</span>}
    </li>
  )
})

/** The images sent with a message, above its words, from their thumbnails; a tap opens one full
 *  size, over its thumbnail (already loaded) until the image itself is in. */
function MessageImages({ images }: { images: Attachment[] }) {
  const [open, setOpen] = useState<Attachment | null>(null)
  const [shown, setShown] = useState<Attachment | null>(null) // kept while it fades out
  const [loaded, setLoaded] = useState<number | null>(null) // the full image that has arrived
  return (
    <>
      <div className="mb-1.5 flex flex-wrap justify-end gap-1.5">
        {images.map((image) => (
          <button key={image.id} type="button" aria-label="Open the image" onClick={() => {
            setShown(image)
            setOpen(image)
          }}
            className="overflow-hidden rounded-xl border bg-secondary transition-opacity hover:opacity-90">
            {/* Its own size, so the timeline doesn't jump when it loads. */}
            <img src={thumbnailUrl(image)} alt="" width={image.width} height={image.height} loading="lazy" decoding="async"
              className={cn('block object-cover', images.length === 1 ? 'max-h-56 w-auto max-w-[min(100%,240px)]' : 'size-[88px]')} />
          </button>
        ))}
      </div>
      <Dialog open={open !== null} onOpenChange={(next) => !next && setOpen(null)}>
        <DialogPortal>
          <DialogOverlay forceRender className="bg-black/80 duration-300 ease-strata" />
          <DialogPrimitive.Popup onClick={() => setOpen(null)}
            className="fixed inset-0 z-50 grid cursor-zoom-out place-items-center p-4 outline-none duration-300 ease-strata
              data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <DialogTitle className="sr-only">Image</DialogTitle>
            {shown && (
              <div className="relative max-h-full max-w-full" style={{ aspectRatio: `${shown.width} / ${shown.height}` }}>
                <img src={thumbnailUrl(shown)} alt="" width={shown.width} height={shown.height}
                  className="max-h-[calc(100dvh-2rem)] max-w-full rounded-lg object-contain" />
                <img src={attachmentUrl(shown.id)} alt="" width={shown.width} height={shown.height} decoding="async"
                  onLoad={() => setLoaded(shown.id)}
                  className={cn('absolute inset-0 size-full rounded-lg object-contain transition-opacity duration-300',
                    loaded === shown.id ? 'opacity-100' : 'opacity-0')} />
              </div>
            )}
          </DialogPrimitive.Popup>
        </DialogPortal>
      </Dialog>
    </>
  )
}

const EventItem = memo(function EventItem({ event, working }: { event: TaskEvent; working: boolean }) {
  const { kind, data } = event
  if (kind === 'command_started' || kind === 'command_finished') {
    // A command shows as it starts, with light passing over it while it runs; one the task
    // stopped without finishing is left still.
    const running = kind === 'command_started' && working
    const failed = kind === 'command_finished' && (data.status !== 'completed' || (data.exit_code ?? 0) !== 0)
    const result = kind === 'command_started' ? (running ? 'running' : 'stopped')
      : data.status === 'declined' ? 'declined' : failed ? `exit ${data.exit_code ?? '?'}` : 'done'
    return (
      <li>
        {/* Quieter than the agent's words, one line each; a tap opens the whole command and its output. */}
        <details className="group text-[13px]">
          <summary className="flex cursor-pointer list-none items-baseline gap-2.5 text-muted-foreground transition-colors hover:text-foreground/80 group-open:text-foreground/90 [&::-webkit-details-marker]:hidden">
            <code className={cn('min-w-0 flex-1 truncate font-mono text-[12.5px] group-open:whitespace-pre-wrap group-open:[overflow-wrap:anywhere]',
              running && 'animate-shimmer')}>
              <span className="text-muted-foreground/60">›</span> {data.command}
            </code>
            <small className={cn('shrink-0 text-xs', failed ? 'text-warn' : 'text-muted-foreground/70')}>{result}</small>
          </summary>
          {data.output_tail && (
            // What it printed: a size down and dimmer than the command, with no box to take up width.
            <pre className="mt-1.5 max-h-64 overflow-auto font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-muted-foreground/70 [overflow-wrap:anywhere]">{data.output_tail}</pre>
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
})

/** How a finished tool call went, when it didn't simply work; a running one says nothing. */
function callResult(call: TaskEvent, working: boolean): string | null {
  const status = call.data.status as string
  return status === 'running' ? (working ? null : 'stopped') : status === 'completed' ? null : status
}

/**
 * Tool calls other than commands and edits (reads, searches, the web, MCP servers), quieter than
 * commands: a run of them is one line, "Read ×12 · Grep ×3", which opens to list each call.
 */
function ToolCalls({ calls, working }: { calls: TaskEvent[]; working: boolean }) {
  const running = working && calls.some((call) => call.data.status === 'running')
  const failed = calls.some((call) => call.data.status === 'failed')
  const counts = new Map<string, number>()
  for (const call of calls) counts.set(call.data.tool, (counts.get(call.data.tool) ?? 0) + 1)
  const line = calls.length === 1 ? [calls[0].data.tool, calls[0].data.summary].filter(Boolean).join(' ')
    : [...counts].map(([tool, count]) => count > 1 ? `${tool} ×${count}` : tool).join(' · ')
  const result = calls.length === 1 ? callResult(calls[0], working) : failed ? 'failed' : null
  return (
    <li>
      <details className="group text-xs">
        <summary className="flex cursor-pointer list-none items-baseline gap-2.5 text-muted-foreground/80 transition-colors hover:text-foreground/80 group-open:text-foreground/90 [&::-webkit-details-marker]:hidden">
          <span className={cn('min-w-0 flex-1 truncate', running && 'animate-shimmer')}>{line}</span>
          {result && <small className={cn('shrink-0 text-xs', failed ? 'text-warn' : 'text-muted-foreground/70')}>{result}</small>}
        </summary>
        <ul className="mt-1.5 flex max-h-64 flex-col gap-1 overflow-auto">
          {calls.map((call) => {
            const said = callResult(call, working)
            return (
              <li key={call.id} className="flex min-w-0 items-baseline gap-2.5">
                <span className="shrink-0 text-foreground/85">{call.data.tool}</span>
                <span className={cn('min-w-0 flex-1 font-mono text-[11.5px] text-muted-foreground [overflow-wrap:anywhere]',
                  working && call.data.status === 'running' && 'animate-shimmer')}>{call.data.summary}</span>
                {said && <small className={cn('shrink-0', call.data.status === 'failed' ? 'text-warn' : 'text-muted-foreground/70')}>{said}</small>}
              </li>
            )
          })}
        </ul>
      </details>
    </li>
  )
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
