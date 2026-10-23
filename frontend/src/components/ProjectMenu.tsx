import { Popover } from '@base-ui/react/popover'
import { Check, ChevronDown } from 'lucide-react'
import type { AgentStatus, Project } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The header's project chooser: where a new task starts. A styled menu rather than the OS list
 * a native select opens on phones, and it says which agent each project runs.
 */
export function ProjectMenu({ projects, current, agents, onChange }: {
  projects: Project[]
  current: Project
  agents: Record<string, AgentStatus> | null
  onChange: (id: number) => void
}) {
  return (
    <Popover.Root>
      <Popover.Trigger aria-label={`Project: ${current.name}`}
        className="flex h-8 min-w-0 items-center gap-2 rounded-full border px-3 text-[13px] text-muted-foreground transition-colors hover:text-foreground data-popup-open:text-foreground">
        <span className="size-1.5 shrink-0 rounded-full bg-foreground/70" />
        <span className="min-w-0 truncate">{current.name}</span>
        <ChevronDown className="size-3 shrink-0" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={8} className="z-50">
          <Popover.Popup className={cn('max-h-[min(60dvh,420px)] w-[min(280px,calc(100vw-32px))] overflow-y-auto rounded-2xl border bg-popover p-2 text-popover-foreground shadow-lg outline-none',
            'origin-(--transform-origin) transition-[opacity,scale] duration-200 ease-strata data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:scale-95 data-ending-style:opacity-0')}>
            <Popover.Title className="eyebrow px-2.5 pt-1.5 pb-1 text-muted-foreground">New tasks go to</Popover.Title>
            <div role="radiogroup" aria-label="Project">
              {projects.map((p) => (
                <Popover.Close key={p.id} render={<button type="button" />} role="radio" aria-checked={p.id === current.id}
                  aria-label={p.name} onClick={() => onChange(p.id)}
                  className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-secondary">
                  <span className="min-w-0 truncate">
                    {p.name} <span className="text-muted-foreground">· {agents?.[p.agent]?.title ?? p.agent}</span>
                  </span>
                  <Check className={cn('size-3.5 shrink-0', p.id !== current.id && 'invisible')} />
                </Popover.Close>
              ))}
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
