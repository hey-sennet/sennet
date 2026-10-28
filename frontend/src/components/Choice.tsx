import { Popover } from '@base-ui/react/popover'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ChoiceOption = { value: string; label: string; note?: string; disabled?: boolean }

const row = 'flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-secondary disabled:pointer-events-none disabled:opacity-50'

/**
 * One of a few options, as the header's project menu and the stage's model menu choose: a pill
 * that opens a styled menu, rather than the OS list a native select opens on phones. Its width
 * is set by `className`, so the menus in a card line up.
 */
export function Choice({ label, value, options, onChange, disabled, className }: {
  label: string // what it chooses, which names its menu
  value: string
  options: ChoiceOption[]
  onChange: (value: string) => void
  disabled?: boolean
  className?: string
}) {
  const current = options.find((o) => o.value === value)
  const shown = current?.label ?? value
  return (
    <Popover.Root>
      <Popover.Trigger aria-label={`${label}: ${shown}`} title={shown} disabled={disabled}
        className={cn('flex h-8 w-56 max-w-full min-w-0 items-center justify-between gap-2 rounded-full border bg-background px-3 text-xs text-foreground transition-colors outline-none',
          'hover:border-ring/60 focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50 data-popup-open:border-ring/60', className)}>
        <span className="min-w-0 truncate">{shown}</span>
        <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="start" sideOffset={6} className="z-50">
          <Popover.Popup className={cn('max-h-[min(60dvh,420px)] w-[min(300px,calc(100vw-32px))] min-w-(--anchor-width) overflow-y-auto rounded-2xl border bg-popover p-2 text-popover-foreground shadow-lg outline-none',
            'origin-(--transform-origin) transition-[opacity,scale] duration-200 ease-strata data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:scale-95 data-ending-style:opacity-0')}>
            <Popover.Title className="eyebrow px-2.5 pt-1.5 pb-1 text-muted-foreground">{label}</Popover.Title>
            <div role="radiogroup" aria-label={label}>
              {options.map((o) => (
                <Popover.Close key={o.value} render={<button type="button" />} role="radio" aria-checked={o.value === value}
                  aria-label={o.label} disabled={o.disabled} onClick={() => onChange(o.value)} className={row}>
                  <span className="min-w-0">
                    {o.label}
                    {o.note && <span className="block text-xs text-muted-foreground">{o.note}</span>}
                  </span>
                  <Check className={cn('size-3.5 shrink-0', o.value !== value && 'invisible')} />
                </Popover.Close>
              ))}
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
