import { useEffect, useState } from 'react'
import { ChevronRight, Folder, GitBranch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, type Folders } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The folders on the computer running Sennet, a level at a time, to pick a project from. The
 * browser's own picker can't do this: it browses the device holding the browser, and gives no
 * real paths. Starts at `start`, or home.
 */
export function FolderBrowser({ start, onPick, onClose }: {
  start?: string
  onPick: (path: string, name: string) => void
  onClose: () => void
}) {
  const [listing, setListing] = useState<Folders | null>(null)
  const [error, setError] = useState<string | null>(null)

  const open = (path?: string) => {
    setError(null)
    api.directories(path).then(setListing, (e) => {
      setError(e instanceof Error ? e.message : 'Could not list the folders')
      if (path && !listing) open() // a typed path that isn't there: start at home instead
    })
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- once, where it starts
  useEffect(() => open(start || undefined), [])

  const crumbs = listing ? breadcrumbs(listing.path, listing.home) : []
  return (
    <div className="flex min-w-0 animate-word-in flex-col rounded-[22px] border bg-card motion-reduce:animate-none">
      <nav aria-label="Folder" className="flex min-w-0 flex-wrap items-center gap-0.5 border-b px-3 py-2.5 text-xs text-muted-foreground">
        {crumbs.map((crumb, i) => (
          <span key={crumb.path} className="flex min-w-0 items-center gap-0.5">
            {i > 0 && <ChevronRight className="size-3 shrink-0" />}
            <button type="button" className={cn('truncate rounded-full px-1.5 py-0.5 hover:text-foreground',
              i === crumbs.length - 1 && 'text-foreground')} onClick={() => open(crumb.path)}>
              {crumb.label}
            </button>
          </span>
        ))}
      </nav>
      <ul className="max-h-64 min-w-0 overflow-y-auto py-1">
        {listing?.directories.map((folder) => (
          <li key={folder.path}>
            <button type="button" className="flex w-full min-w-0 items-center gap-2.5 px-4 py-2 text-left text-sm hover:bg-muted/50"
              onClick={() => open(folder.path)}>
              <Folder className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{folder.name}</span>
              {folder.repo && <GitBranch aria-label="git repository" className="ml-auto size-3.5 shrink-0 text-agent" />}
            </button>
          </li>
        ))}
        {listing && !listing.directories.length && <li className="px-4 py-2 text-sm text-muted-foreground">No folders in here.</li>}
        {!!listing?.more && <li className="px-4 py-2 text-xs text-muted-foreground">And {listing.more} more; type the path to reach them.</li>}
        {!listing && !error && <li className="px-4 py-2 text-sm text-muted-foreground">Loading…</li>}
      </ul>
      {error && <p className="px-4 pb-2 text-xs text-warn">{error}</p>}
      <div className="flex items-center justify-between gap-2 border-t py-2 pr-2 pl-4">
        <span className="min-w-0 truncate text-xs text-muted-foreground">{listing?.repo ? 'A git repository' : ''}</span>
        <span className="flex shrink-0 gap-1">
          <Button type="button" variant="ghost" size="sm" className="rounded-full" onClick={onClose}>Cancel</Button>
          <Button type="button" size="sm" className="rounded-full px-4" disabled={!listing}
            onClick={() => listing && onPick(listing.path, listing.path.split('/').filter(Boolean).pop() ?? '')}>
            Use this folder
          </Button>
        </span>
      </div>
    </div>
  )
}

/** Each folder from home (or the root, outside it) down to `path`, to click back up to. */
export function breadcrumbs(path: string, home: string): { label: string; path: string }[] {
  const inHome = path === home || path.startsWith(home.endsWith('/') ? home : `${home}/`)
  const base = inHome ? home : '/'
  const crumbs = [{ label: inHome ? 'Home' : '/', path: base }]
  let current = base
  for (const part of path.slice(base.length).split('/').filter(Boolean)) {
    current = current.endsWith('/') ? `${current}${part}` : `${current}/${part}`
    crumbs.push({ label: part, path: current })
  }
  return crumbs
}
