import { useEffect, useState } from 'react'
import { GitBranch, GitMerge, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type TaskDetail, type WorktreeStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from './ConfirmDialog'

const FINISHED = ['completed', 'failed', 'cancelled', 'unknown']

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** The files, by name, up to a few: "a.ts, b.ts and 3 more". */
function named(files: string[], shown = 3): string {
  const names = files.slice(0, shown).map((f) => f.split('/').pop())
  return files.length > shown ? `${names.join(', ')} and ${files.length - shown} more` : names.join(', ')
}

/**
 * A task's own worktree (src/sennet/worktrees.py): its branch and what it changed and, once the
 * task is done, merging it into the branch it came from and removing it. A merge that would
 * conflict isn't started: the agent can be asked to resolve it on its branch.
 */
export function Worktree({ detail, className }: { detail: TaskDetail; className?: string }) {
  const [status, setStatus] = useState<WorktreeStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [conflicted, setConflicted] = useState<{ task: number; files: string[] } | null>(null) // from the last merge tried
  const conflicts = conflicted?.task === detail.id ? conflicted.files : null
  const [removing, setRemoving] = useState(false)
  const [deleteBranch, setDeleteBranch] = useState(false)
  const finished = FINISHED.includes(detail.status)

  // Asked again as the task moves on: each turn can change its files.
  useEffect(() => {
    if (!detail.worktree) return
    let current = true
    api.worktree(detail.id).then((found) => current && setStatus(found), () => current && setStatus(null))
    return () => {
      current = false
    }
  }, [detail.id, detail.worktree, detail.status, detail.updated_at, detail.branch])

  if (!detail.worktree || !status) return null
  const { base, branch } = status
  const changed = status.files.length > 0 || status.commits > 0
  const mergeable = finished && branch && base && changed
  const elsewhere = base && status.checked_out !== base // the project's checkout is on another branch

  const act = async <T,>(action: () => Promise<T>, failure: string): Promise<T | null> => {
    setBusy(true)
    try {
      return await action()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : failure)
      return null
    } finally {
      setBusy(false)
    }
  }
  const merge = async () => {
    const result = await act(() => api.mergeWorktree(detail.id), 'Could not merge')
    if (!result) return
    setStatus(result.status)
    setConflicted(result.merged ? null : { task: detail.id, files: result.conflicts })
    if (!result.merged) return
    toast.success(result.commits ? `Merged into ${base}` : `Nothing to merge into ${base}`)
  }
  const remove = async () => {
    setRemoving(false)
    const result = await act(() => api.removeWorktree(detail.id, deleteBranch), 'Could not remove the worktree')
    if (result) setStatus(result)
  }
  const resolve = async () => {
    const files = conflicts?.length ? ` in ${conflicts.join(', ')}` : ''
    const sent = await act(() => api.followUp(detail.id, `Merge ${base} into this branch and resolve the conflicts${files}, then commit.`),
      'Could not ask the agent')
    if (sent) setConflicted(null)
  }

  const said = !branch && !status.exists ? 'Its worktree and branch were removed.'
    : !status.exists ? 'Its worktree was removed; the branch is kept, and a follow-up puts the worktree back.'
    : changed ? [
      plural(status.files.length, 'file') + ' changed',
      status.commits ? `${plural(status.commits, 'commit')} not in ${base ?? 'the base'}` : '',
      status.uncommitted ? `${status.uncommitted} uncommitted` : '',
    ].filter(Boolean).join(', ')
    : base ? `Nothing to merge into ${base}.` : 'No changes yet.'

  return (
    <section aria-label="Worktree" className={cn('rounded-2xl border px-3.5 py-2.5 text-xs', className)}>
      <p className="flex min-w-0 items-center gap-1.5">
        <GitBranch className="size-3.5 shrink-0 text-muted-foreground" />
        <code className={cn('min-w-0 truncate font-mono text-[12.5px]', !branch && 'line-through opacity-60')} title={detail.branch ?? undefined}>
          {branch ?? detail.worktree.split('/').pop()}
        </code>
        {base && <span className="shrink-0 text-muted-foreground">from {base}</span>}
      </p>
      <p className="mt-1 text-muted-foreground" title={status.files.join('\n')}>
        {said}
      </p>
      {conflicts && (
        <div className="mt-2 flex flex-col items-start gap-1.5">
          <p className="text-warn">
            Merging would conflict{conflicts.length ? ` in ${named(conflicts)}` : ''}, so nothing was merged.
          </p>
          <Button variant="secondary" size="sm" disabled={busy} onClick={resolve}>Ask the agent to resolve it</Button>
        </div>
      )}
      {finished && (status.exists || branch) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {mergeable && (
            <Button variant="secondary" size="sm" disabled={busy || !!elsewhere} onClick={merge}>
              <GitMerge /> Merge into {base}
            </Button>
          )}
          {(status.exists || branch) && (
            <Button variant="ghost" size="sm" className="text-muted-foreground" disabled={busy} onClick={() => {
              setDeleteBranch(!changed || !status.exists)
              setRemoving(true)
            }}>
              <Trash2 /> {status.exists ? 'Remove worktree' : 'Delete branch'}
            </Button>
          )}
          {mergeable && elsewhere && (
            <span className="basis-full text-muted-foreground">
              The project has {status.checked_out ?? 'no branch'} checked out; check out {base} to merge.
            </span>
          )}
        </div>
      )}
      <ConfirmDialog open={removing} title={status.exists ? 'Remove the worktree?' : `Delete ${branch}?`}
        action={status.exists ? 'Remove' : 'Delete'} onConfirm={remove} onCancel={() => setRemoving(false)}>
        {status.exists && status.uncommitted > 0 && `${plural(status.uncommitted, 'file')} left uncommitted in it will be lost. `}
        {status.exists ? 'Its folder is removed; the task and its conversation stay.' : 'Its commits not merged anywhere will be lost.'}
        {status.exists && branch && (
          <label className="mt-3 flex items-center gap-2 text-foreground">
            <input type="checkbox" checked={deleteBranch} onChange={(event) => setDeleteBranch(event.target.checked)} />
            Delete its branch too{status.commits ? `, with ${plural(status.commits, 'commit')} not in ${base}` : ''}
          </label>
        )}
      </ConfirmDialog>
    </section>
  )
}
