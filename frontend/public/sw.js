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
      data: { task_id: notice.task_id },
    })
  })())
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const taskId = event.notification.data && event.notification.data.task_id
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
