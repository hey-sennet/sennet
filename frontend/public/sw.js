// Sennet's service worker: push notifications only, nothing cached (src/sennet/push.py).
// Settings registers it when notifications are turned on for this device.

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

async function showing() {
  const pages = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  return pages.some((page) => page.visibilityState === 'visible')
}

self.addEventListener('push', (event) => {
  const notice = event.data ? event.data.json() : null
  if (!notice) return
  event.waitUntil((async () => {
    // A page showing hears it there; the server holds back too, but may not know the page is up yet.
    if (notice.kind !== 'test' && await showing()) return
    await self.registration.showNotification(notice.title, {
      body: notice.body,
      icon: '/icon-192.png',
      badge: '/badge-96.png', // Android tints its shape; a full-colour icon shows as a square
      tag: notice.task_id ? `task-${notice.task_id}` : 'sennet', // a task's newest replaces its last
      renotify: true,
      // An approval is answered here, where the browser shows actions (not iOS), unless it's one its
      // card shows in red: that only opens, so it's never approved from a lock screen.
      actions: notice.kind !== 'approval' ? []
        : notice.answerable ? [{ action: 'approve', title: 'Approve' }, { action: 'decline', title: 'Decline' }]
        : [{ action: 'open', title: 'Open' }],
      data: { task_id: notice.task_id, approval_id: notice.approval_id },
    })
  })())
})

// The answer goes as the card's would, from this device; another device sees it as given there.
async function answer(approvalId, taskId, decision) {
  let failed = 'Your answer didn’t reach Sennet.'
  try {
    const response = await fetch(`/api/approvals/${approvalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Sennet': '1' },
      body: JSON.stringify({ decision, via: 'notification' }),
    })
    if (response.ok) return
    if (response.status === 404) failed = 'It was already answered, or the task stopped.'
  } catch {
    // Offline, or Sennet isn't reachable from here.
  }
  await self.registration.showNotification('Approval not answered', {
    body: `${failed} Tap to open the task.`,
    icon: '/icon-192.png',
    badge: '/badge-96.png',
    tag: `task-${taskId}`,
    data: { task_id: taskId },
  })
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const { task_id: taskId, approval_id: approvalId } = event.notification.data ?? {}
  if ((event.action === 'approve' || event.action === 'decline') && approvalId) {
    event.waitUntil(answer(approvalId, taskId, event.action))
    return
  }
  event.waitUntil((async () => {
    const pages = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const page = pages.find((p) => p.visibilityState === 'visible') ?? pages[0]
    if (page) {
      await page.focus()
      if (taskId) page.postMessage({ type: 'open-task', task_id: taskId })
      return
    }
    await self.clients.openWindow(taskId ? `/?task=${taskId}` : '/')
  })())
})

// The browser renewed the subscription: tell the server where it goes now, keeping what notifies.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription
    const subscription = event.newSubscription
      ?? (old && await self.registration.pushManager.subscribe(old.options))
    if (!subscription) return
    await fetch('/api/push', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Sennet': '1' },
      body: JSON.stringify(subscription.toJSON()),
    })
  })())
})
