import { useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogDescription, DialogOverlay, DialogPortal, DialogTitle } from '@/components/ui/dialog'

/**
 * Asks before something that can't be undone, in place of the browser's confirm(): a sheet that
 * rises from the bottom on a phone, under the thumb, and a card in the middle on a wider screen.
 * Rendered where it's used, so one asked from inside Settings is nested in it.
 */
export function ConfirmDialog({ open, title, children, action, onConfirm, onCancel }: {
  open: boolean
  title: string
  children: React.ReactNode // what happens, and what doesn't
  action: string // the button that goes ahead: "Delete", "Unpair"
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogPortal>
        {/* Forced, since Base UI leaves it out when nested (in Settings) and taps would reach the form beneath. It fades
            as long as the sheet moves: a shorter fade ends early and the dim snaps back until the sheet is gone. */}
        <DialogOverlay forceRender className="bg-black/40 duration-300 ease-strata" />
        <DialogPrimitive.Popup role="alertdialog"
          className="fixed inset-x-0 bottom-0 z-50 flex flex-col gap-6 rounded-t-[28px] border border-b-0 border-border bg-background px-6 pt-7
            pb-[max(1.5rem,env(safe-area-inset-bottom))] text-sm outline-none duration-300 ease-strata
            data-open:animate-in data-open:slide-in-from-bottom data-closed:animate-out data-closed:slide-out-to-bottom
            sm:inset-x-auto sm:top-1/2 sm:bottom-auto sm:left-1/2 sm:w-[calc(100%-2rem)] sm:max-w-sm sm:-translate-x-1/2 sm:-translate-y-1/2
            sm:rounded-[28px] sm:border-b sm:p-7 sm:data-open:fade-in-0 sm:data-open:zoom-in-95 sm:data-open:slide-in-from-bottom-0
            sm:data-closed:fade-out-0 sm:data-closed:zoom-out-95 sm:data-closed:slide-out-to-bottom-0">
          {/* A grabber, so the sheet reads as one on a phone. */}
          <span aria-hidden className="mx-auto -mt-3 h-1 w-9 rounded-full bg-muted-foreground/30 sm:hidden" />
          <div className="flex flex-col gap-2">
            <DialogTitle className="font-serif text-[24px] leading-[1.15] font-normal tracking-[-0.01em] [overflow-wrap:anywhere]">{title}</DialogTitle>
            <DialogDescription>{children}</DialogDescription>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row-reverse">
            <Button className="h-11 rounded-full bg-destructive/90 px-5 text-[15px] text-white hover:bg-destructive sm:h-9 sm:text-sm" onClick={onConfirm}>{action}</Button>
            <Button variant="ghost" className="h-11 rounded-full px-5 text-[15px] text-muted-foreground sm:h-9 sm:text-sm" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPortal>
    </Dialog>
  )
}

/** What a ConfirmDialog asks about. It stays once answered, so the dialog keeps its words while it
 * slides away. */
export function useConfirm<T>() {
  const [asking, setAsking] = useState<{ subject: T; open: boolean } | null>(null)
  return {
    subject: asking?.subject,
    open: !!asking?.open,
    ask: (subject: T) => setAsking({ subject, open: true }),
    close: () => setAsking((current) => current && { ...current, open: false }),
  }
}
