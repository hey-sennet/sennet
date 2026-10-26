// Images waiting to go with the next message, typed or spoken: shrunk and uploaded as soon as
// they're added, so sending names them by id (src/sennet/attachments.py). One tray for the page,
// whichever task is on screen: what you add goes with what you say or type next.
import { useSyncExternalStore } from 'react'
import { ApiError, api, type Attachment } from './api'
import { prepare, type Prepared } from './images'

export const MAX_IMAGES = 8 // the server's MAX_PER_MESSAGE
// A dropped connection (a phone between networks) or a server error (Sennet restarting behind its
// proxy) is tried again after these waits; a refusal (not an image, too big) isn't.
const RETRY_MS = [1000, 3000]

export interface Pending {
  key: number
  preview: string | null // an object URL of its thumbnail, once made
  state: 'uploading' | 'ready' | 'failed'
  attachment?: Attachment
  error?: string
  retry: boolean // failed in a way worth trying again
}

interface Steps {
  prepare: (source: Blob | ImageBitmap) => Promise<Prepared>
  upload: (image: Blob) => Promise<Attachment>
  uploadThumbnail: (id: number, thumbnail: Blob) => Promise<Attachment>
  remove: (id: number) => Promise<void>
  url: (image: Blob) => string
  revoke: (url: string) => void
  wait: (ms: number) => Promise<void>
}

export class Tray {
  private items: readonly Pending[] = []
  private keys = 0
  private readonly prepared = new Map<number, Prepared>() // by key, to upload again after a failure
  private readonly listeners = new Set<() => void>()
  private readonly steps: Steps

  constructor(steps: Steps = {
    prepare, upload: api.uploadImage, uploadThumbnail: api.uploadThumbnail, remove: api.removeAttachment,
    url: (image) => URL.createObjectURL(image), revoke: (url) => URL.revokeObjectURL(url),
    wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  }) {
    this.steps = steps
  }

  readonly get = () => this.items

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => void this.listeners.delete(listener)
  }

  /** Ids of the images ready to send, in the order they were added. */
  ids(): number[] {
    return this.items.flatMap((item) => (item.state === 'ready' && item.attachment ? [item.attachment.id] : []))
  }

  get uploading(): boolean {
    return this.items.some((item) => item.state === 'uploading')
  }

  /** Add images, from files or camera frames; past MAX_IMAGES the rest are left out, and the count of those returned. */
  add(images: (Blob | ImageBitmap)[]): number {
    const room = Math.max(0, MAX_IMAGES - this.items.length)
    for (const image of images.slice(0, room)) void this.start(image)
    for (const image of images.slice(room)) if (!(image instanceof Blob)) image.close()
    return images.length - Math.min(images.length, room)
  }

  private async start(source: Blob | ImageBitmap) {
    const key = ++this.keys
    this.set([...this.items, { key, preview: null, state: 'uploading', retry: false }])
    let prepared: Prepared
    try {
      prepared = await this.steps.prepare(source)
    } catch {
      return this.change(key, { state: 'failed', error: "Couldn't read that image", retry: false })
    }
    if (!this.has(key)) return // taken out while it was made ready
    this.prepared.set(key, prepared)
    this.change(key, { preview: this.steps.url(prepared.thumbnail) })
    await this.upload(key)
  }

  /** Try a failed upload again. */
  retry(key: number) {
    const item = this.items.find((i) => i.key === key)
    if (item?.state !== 'failed' || !this.prepared.has(key)) return
    this.change(key, { state: 'uploading', error: undefined, retry: false })
    void this.upload(key)
  }

  private async upload(key: number) {
    const prepared = this.prepared.get(key)!
    try {
      let attachment = this.items.find((i) => i.key === key)?.attachment
      attachment ??= await this.patiently(() => this.steps.upload(prepared.image))
      if (!this.has(key)) {
        void this.steps.remove(attachment.id).catch(() => {}) // taken out while it uploaded
        return
      }
      this.change(key, { attachment })
      // The timeline shows it from the thumbnail, so the thumbnail is in before it can be sent;
      // without one, it shows from the image itself.
      attachment = await this.patiently(() => this.steps.uploadThumbnail(attachment!.id, prepared.thumbnail))
        .catch(() => attachment!)
      this.change(key, { state: 'ready', attachment })
      this.prepared.delete(key)
    } catch (error) {
      this.change(key, { state: 'failed', retry: passing(error),
                         error: error instanceof Error ? error.message : "Couldn't add that image" })
    }
  }

  /** `request`, tried again after a dropped connection; a refusal from the server is final. */
  private async patiently<T>(request: () => Promise<T>): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await request()
      } catch (error) {
        if (!passing(error) || attempt >= RETRY_MS.length) throw error
        await this.steps.wait(RETRY_MS[attempt])
      }
    }
  }

  private has(key: number) {
    return this.items.some((item) => item.key === key)
  }

  /** Take one out; the server forgets it too. */
  remove(key: number) {
    const item = this.items.find((i) => i.key === key)
    if (!item) return
    this.set(this.items.filter((i) => i !== item))
    this.prepared.delete(key)
    if (item.preview) this.steps.revoke(item.preview)
    if (item.attachment) void this.steps.remove(item.attachment.id).catch(() => {})
  }

  /** These went with a message: out of the tray, kept on the server with it. */
  sent(ids: number[]) {
    const gone = this.items.filter((item) => item.attachment && ids.includes(item.attachment.id))
    if (!gone.length) return
    this.set(this.items.filter((item) => !gone.includes(item)))
    gone.forEach((item) => {
      this.prepared.delete(item.key)
      if (item.preview) this.steps.revoke(item.preview)
    })
  }

  private change(key: number, fields: Partial<Pending>) {
    const item = this.items.find((i) => i.key === key)
    if (!item) return
    if (fields.preview && item.preview && fields.preview !== item.preview) this.steps.revoke(item.preview)
    this.set(this.items.map((i) => (i === item ? { ...i, ...fields } : i)))
  }

  private set(items: readonly Pending[]) {
    this.items = items
    this.listeners.forEach((listener) => listener())
  }
}

/** A failure that may not happen again: the connection, or the server, rather than the image. */
function passing(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500
}

let shared: Tray | null = null

export function tray(): Tray {
  shared ??= new Tray()
  return shared
}

export function useTray(): readonly Pending[] {
  const t = tray()
  return useSyncExternalStore(t.subscribe, t.get)
}

/** Images into the tray (anything that isn't an image is left out); what didn't fit, said for a toast. */
export function addImages(files: Iterable<File>): string | null {
  const left = tray().add([...files].filter((file) => file.type.startsWith('image/')))
  return left ? `A message takes up to ${MAX_IMAGES} images; ${left === 1 ? 'one was' : `${left} were`} left out` : null
}
