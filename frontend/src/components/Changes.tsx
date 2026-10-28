import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, type ChangedFile, type FileDiff, type TaskChanges, type TaskDetail } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Diff } from './Diff'

const SHOWN_LINES = 300 // of a diff, until "Show all"

const STATE: Record<NonNullable<TaskChanges['state']>, (branch: string, base: string | null) => string> = {
  uncommitted: (branch) => `Uncommitted, on ${branch}`,
  committed: (branch) => `Committed to ${branch}, not pushed`,
  pushed: (branch) => `Committed to ${branch} and pushed`,
  merged: (_, base) => `Merged into ${base}`,
}

/**
 * What a task changed, in one view (src/sennet/changes.py): each file with its lines added and
 * removed, opening to its net diff, and where the change is, uncommitted, committed or pushed.
 * Read-only: committing or undoing it is something to ask the agent.
 */
export function Changes({ detail }: { detail: TaskDetail }) {
  const [found, setFound] = useState<{ task: number; changes: TaskChanges | null; error?: string } | null>(null)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const changes = found?.task === detail.id ? found.changes : null

  // Asked again as the task moves on: each turn and edit can change it.
  useEffect(() => {
    let current = true
    api.changes(detail.id).then((result) => current && setFound({ task: detail.id, changes: result }),
      (error) => current && setFound({ task: detail.id, changes: null, error: error instanceof Error ? error.message : 'Could not load the changes' }))
    return () => {
      current = false
    }
  }, [detail.id, detail.status, detail.updated_at, detail.events.length])

  if (found?.task === detail.id && found.error) return <p className="mt-7 text-sm text-warn">{found.error}</p>
  if (!changes) return <p className="mt-7 text-sm text-muted-foreground">Looking at what it changed…</p>
  const added = changes.files.reduce((sum, f) => sum + f.added, 0)
  const removed = changes.files.reduce((sum, f) => sum + f.removed, 0)
  const toggle = (path: string) => setOpen((now) => {
    const next = new Set(now)
    if (!next.delete(path)) next.add(path)
    return next
  })

  return (
    <section aria-label="Changes" className="mt-7 flex flex-col gap-4 text-[13px]">
      <div className="flex flex-col gap-0.5 text-xs text-muted-foreground">
        {changes.files.length > 0 && (
          <p className="text-foreground/90">
            {changes.files.length + changes.more} {changes.files.length + changes.more === 1 ? 'file' : 'files'}
            {(added > 0 || removed > 0) && <> · <span className="text-agent">+{added}</span> <span className="text-destructive">−{removed}</span></>}
          </p>
        )}
        {changes.state && changes.branch && <p>{STATE[changes.state](changes.branch, changes.base)}</p>}
        {changes.estimated && changes.files.length > 0 && (
          <p>Counted from where the project was when the task was made.</p>
        )}
        {!changes.net && changes.files.length > 0 && (
          <p>{changes.gone ? 'Its worktree was removed, so these' : 'These'} are its edits as it made them, one after another, not added up.</p>
        )}
      </div>
      {changes.files.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing changed yet.</p>
      ) : groups(changes.files).map(([label, files]) => (
        <div key={label}>
          {label && <p className="mb-1 text-xs font-medium text-muted-foreground">{label} · {files.length}</p>}
          <ul className="flex flex-col gap-1">
            {files.map((file) => (
              <FileRow key={file.path} task={detail.id} file={file} open={open.has(file.path)} onToggle={() => toggle(file.path)}
                version={`${detail.updated_at}:${detail.events.length}`} />
            ))}
          </ul>
        </div>
      ))}
      {changes.more > 0 && <p className="text-xs text-muted-foreground">and {changes.more} more files</p>}
      {changes.elsewhere.length > 0 && (
        <p className="text-xs text-muted-foreground" title={changes.elsewhere.join('\n')}>
          Also changed in the project since it started, not by its edits: {changes.elsewhere.map((p) => p.split('/').pop()).join(', ')}
        </p>
      )}
    </section>
  )
}

/** From git, files not committed and then committed, each under its heading; else one list. */
function groups(files: ChangedFile[]): [string | null, ChangedFile[]][] {
  if (files.every((f) => f.committed === undefined)) return [[null, files]]
  return ([['Not committed', files.filter((f) => !f.committed)], ['Committed', files.filter((f) => f.committed)]] as [string, ChangedFile[]][])
    .filter(([, group]) => group.length > 0)
}

function FileRow({ task, file, open, onToggle, version }: {
  task: number
  file: ChangedFile
  open: boolean
  onToggle: () => void
  version: string // the task as it was: an open diff is asked again when it moves on
}) {
  const [diff, setDiff] = useState<{ key: string; diff: FileDiff | null; error?: string } | null>(null)
  const [all, setAll] = useState(false)
  const key = `${task}:${file.path}:${version}`
  const shown = diff?.key === key ? diff : null

  useEffect(() => {
    if (!open || file.binary) return
    let current = true
    api.changeDiff(task, file.path).then((found) => current && setDiff({ key, diff: found }),
      (error) => current && setDiff({ key, diff: null, error: error instanceof Error ? error.message : 'Could not load the diff' }))
    return () => {
      current = false
    }
  }, [open, task, file.path, file.binary, key])

  const slash = file.path.lastIndexOf('/')
  const lines = shown?.diff?.diff.split('\n') ?? []
  const cut = !all && lines.length > SHOWN_LINES
  return (
    <li>
      <button type="button" aria-expanded={open} onClick={onToggle} title={file.path}
        className="flex w-full min-w-0 items-baseline gap-2 rounded-lg py-1 text-left hover:text-foreground">
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">
          {slash >= 0 && <span className="text-muted-foreground">{file.path.slice(0, slash + 1)}</span>}
          {file.path.slice(slash + 1)}
        </span>
        {file.kind === 'add' && <small className="shrink-0 text-xs text-agent">new</small>}
        {file.kind === 'delete' && <small className="shrink-0 text-xs text-destructive">deleted</small>}
        {file.binary ? <small className="shrink-0 text-xs text-muted-foreground">binary</small> : (
          <small className="shrink-0 text-xs tabular-nums">
            <span className="text-agent">+{file.added}</span> <span className="text-destructive">−{file.removed}</span>
          </small>
        )}
        <ChevronDown className={cn('size-3.5 shrink-0 self-center text-muted-foreground transition-transform duration-300 ease-strata', open && 'rotate-180')} />
      </button>
      {open && !file.binary && (
        shown?.error ? <p className="mt-1 text-xs text-warn">{shown.error}</p>
          : !shown?.diff ? <p className="mt-1 text-xs text-muted-foreground">Loading…</p>
          : (
            <div className="mt-1.5 mb-2">
              <Diff diff={cut ? lines.slice(0, SHOWN_LINES).join('\n') : shown.diff.diff} className="max-h-[60dvh]" />
              {cut && (
                <Button variant="ghost" size="xs" className="mt-1 text-muted-foreground" onClick={() => setAll(true)}>
                  Show all {lines.length.toLocaleString()} lines
                </Button>
              )}
              {shown.diff.truncated && (all || !cut) && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Cut at {lines.length.toLocaleString()} of {shown.diff.lines.toLocaleString()} lines.
                </p>
              )}
            </div>
          )
      )}
    </li>
  )
}
