import { api, type PushKind } from './api'

/** Push notifications on this device (src/sennet/push.py; the service worker is public/sw.js). */

export const PUSH_KINDS: PushKind[] = ['approval', 'finished', 'failed']

// supported; insecure: not over https; install: an iPhone or iPad, where only a Home Screen app has push.
export type PushSupport = 'supported' | 'insecure' | 'install' | 'unsupported'

export function pushSupport(): PushSupport {
  if (!window.isSecureContext) return 'insecure'
  if ('serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window) return 'supported'
  const apple = /iPhone|iPad/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
  return apple ? 'install' : 'unsupported'
}

const bytes = (key: string) => Uint8Array.from(atob(key.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0))

function sameKey(key: ArrayBuffer | null, expected: string) {
  const want = bytes(expected)
  const have = key ? new Uint8Array(key) : null
  return !!have && have.length === want.length && have.every((byte, i) => byte === want[i])
}

/** What notifies this device; null when it's off. On only while the server has this browser's subscription. */
export async function pushChoice(): Promise<PushKind[] | null> {
  const [state, registration] = await Promise.all([api.push(), navigator.serviceWorker.getRegistration('/')])
  const subscription = await registration?.pushManager.getSubscription()
  if (Notification.permission !== 'granted' || !state.subscription || subscription?.endpoint !== state.subscription.endpoint) return null
  return state.subscription.kinds
}

/** Ask to notify (so a tap must start this), subscribe, and tell the server what notifies. */
export async function enablePush(kinds: PushKind[]): Promise<PushKind[]> {
  const permission = await Notification.requestPermission()
  if (permission === 'denied') throw new Error('Notifications are blocked for this site in the browser\'s settings')
  if (permission !== 'granted') throw new Error('Notifications weren\'t allowed')
  const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  await navigator.serviceWorker.ready
  const { key } = await api.push()
  let subscription = await registration.pushManager.getSubscription()
  if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe() // made for another server key, as after a new data directory
    subscription = null
  }
  subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(key) })
  return (await api.subscribePush(subscription.toJSON(), kinds)).kinds
}

/** Change what notifies, on the subscription this device has. */
export async function choosePush(kinds: PushKind[]): Promise<PushKind[]> {
  const registration = await navigator.serviceWorker.getRegistration('/')
  const subscription = await registration?.pushManager.getSubscription()
  if (!subscription) return enablePush(kinds)
  return (await api.subscribePush(subscription.toJSON(), kinds)).kinds
}

export async function disablePush() {
  await api.unsubscribePush()
  const registration = await navigator.serviceWorker.getRegistration('/')
  await (await registration?.pushManager.getSubscription())?.unsubscribe()
}

/** Open the task a notification was about: tapped with a page open, the service worker says so;
 * with none, it opens one at `/?task=<id>`. */
export function onNotificationOpened(open: (taskId: number) => void): () => void {
  const asked = Number(new URLSearchParams(location.search).get('task'))
  if (asked) {
    history.replaceState(null, '', location.pathname)
    open(asked)
  }
  const onMessage = (event: MessageEvent) => {
    if (event.data?.type === 'open-task' && typeof event.data.task_id === 'number') open(event.data.task_id)
  }
  navigator.serviceWorker?.addEventListener('message', onMessage)
  return () => navigator.serviceWorker?.removeEventListener('message', onMessage)
}
