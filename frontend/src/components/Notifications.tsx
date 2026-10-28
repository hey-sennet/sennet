import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type PushKind } from '@/lib/api'
import { choosePush, disablePush, enablePush, PUSH_KINDS, pushChoice, pushSupport } from '@/lib/push'

const LABELS: Record<PushKind, string> = {
  approval: 'waits for your approval',
  finished: 'finishes',
  failed: 'fails',
}

const failed = (error: unknown, fallback: string) => toast.error(error instanceof Error ? error.message : fallback)

/**
 * Whether notifications are blocked for Sennet, kept current: allowing them in the site settings
 * shows here without reopening Settings.
 */
function useBlocked() {
  const [blocked, setBlocked] = useState(() => typeof Notification !== 'undefined' && Notification.permission === 'denied')
  useEffect(() => {
    let status: PermissionStatus | undefined
    let gone = false
    const update = () => setBlocked(status?.state === 'denied')
    // Not Notification.permission alone: headless Chromium says denied there even when it's been granted.
    navigator.permissions?.query({ name: 'notifications' }).then((result) => {
      if (gone) return
      status = result
      update()
      result.addEventListener('change', update)
    }, () => {})
    return () => {
      gone = true
      status?.removeEventListener('change', update)
    }
  }, [])
  return blocked
}

/** Settings' push notifications for this device: on or off, and what notifies. */
export function Notifications() {
  const [support] = useState(pushSupport)
  // undefined while checking; null when off
  const [kinds, setKinds] = useState<PushKind[] | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const blocked = useBlocked()

  useEffect(() => {
    if (support === 'supported') pushChoice().then(setKinds, () => setKinds(null))
  }, [support])

  if (support === 'insecure') {
    return <p className="text-sm text-muted-foreground">Notifications need Sennet over HTTPS, such as at its Tailscale address.</p>
  }
  if (support === 'install') {
    return (
      <p className="text-sm text-muted-foreground">
        On an iPhone or iPad, add Sennet to your Home Screen first (Share, then Add to Home Screen), and turn
        them on here in that app.
      </p>
    )
  }
  if (support === 'unsupported') return <p className="text-sm text-muted-foreground">This browser can't show notifications.</p>
  if (kinds === undefined) return <p className="text-sm text-muted-foreground">Checking…</p>

  const run = async (action: () => Promise<PushKind[] | null>, failure: string) => {
    setBusy(true)
    try {
      setKinds(await action())
    } catch (error) {
      failed(error, failure)
    } finally {
      setBusy(false)
    }
  }
  const toggle = (on: boolean) => run(on ? () => enablePush(PUSH_KINDS) : async () => (await disablePush(), null),
    on ? 'Could not turn notifications on' : 'Could not turn notifications off')
  const choose = (kind: PushKind, on: boolean) => {
    const next = PUSH_KINDS.filter((k) => (k === kind ? on : kinds?.includes(k)))
    return run(() => choosePush(next), 'Could not change what notifies')
  }

  const installed = matchMedia('(display-mode: standalone)').matches

  return (
    <div className="flex flex-col gap-3 text-sm">
      <label className="flex items-start gap-2">
        {/* Blocked can't be turned on from here, but a subscription made before it was blocked can still be turned off. */}
        <input type="checkbox" className="mt-0.5" checked={kinds !== null} disabled={busy || (blocked && kinds === null)}
          onChange={(event) => toggle(event.target.checked)} />
        <span>
          Notify this device
          <span className="block text-xs text-muted-foreground">
            While Sennet isn't showing here. Tapping one opens its task.
            {blocked && (installed
              ? ' Notifications are blocked for Sennet. Allow them in its app info (long-press its icon), then turn them on here.'
              : ' Notifications are blocked for this site. Allow them in its site settings (the icon left of the address), then turn them on here.')}
          </span>
        </span>
      </label>
      {kinds !== null && (
        <div className="flex flex-col gap-1.5 pl-6">
          <span className="text-xs text-muted-foreground">When a task…</span>
          {PUSH_KINDS.map((kind) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="checkbox" checked={kinds.includes(kind)} disabled={busy}
                onChange={(event) => choose(kind, event.target.checked)} />
              {LABELS[kind]}
            </label>
          ))}
          <Button variant="outline" size="sm" className="mt-1 self-start rounded-full px-4" disabled={busy}
            onClick={() => api.testPush().then(() => toast.success('Sent a test notification'),
              (error) => failed(error, 'Could not send a test notification'))}>
            Send a test
          </Button>
        </div>
      )}
    </div>
  )
}
