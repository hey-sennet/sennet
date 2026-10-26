import { describe, expect, it, vi } from 'vitest'
import { ApiError, type Attachment } from './api'
import { fitted } from './images'
import { MAX_IMAGES, Tray } from './tray'

const image = (id: number, thumbnail: Attachment['thumbnail'] = null): Attachment =>
  ({ id, media_type: 'image/webp', width: 10, height: 10, thumbnail })

type Call<T> = { resolve: (value: T) => void; reject: (error: Error) => void }

function harness() {
  let urls = 0
  const uploads: Call<Attachment>[] = []
  const thumbnails: (Call<Attachment> & { id: number })[] = []
  const waits: number[] = []
  const steps = {
    prepare: vi.fn(async (source: Blob | ImageBitmap) => ({ image: source as Blob, thumbnail: new Blob(['t']) })),
    upload: vi.fn(() => new Promise<Attachment>((resolve, reject) => uploads.push({ resolve, reject }))),
    uploadThumbnail: vi.fn((id: number) => new Promise<Attachment>((resolve, reject) => thumbnails.push({ id, resolve, reject }))),
    remove: vi.fn(async () => {}),
    url: vi.fn(() => `blob:${++urls}`),
    revoke: vi.fn(),
    wait: vi.fn(async (ms: number) => void waits.push(ms)),
  }
  return { tray: new Tray(steps), steps, uploads, thumbnails, waits }
}

const settle = () => new Promise((resolve) => setTimeout(resolve))

/** Upload number `i` succeeds as image `id`, and then its thumbnail. */
async function uploaded(h: ReturnType<typeof harness>, i: number, id: number) {
  h.uploads[i].resolve(image(id))
  await settle()
  h.thumbnails.find((t) => t.id === id)!.resolve(image(id, 'image/webp'))
  await settle()
}

describe('the tray', () => {
  it('is ready to send an image once it and its thumbnail are uploaded', async () => {
    const h = harness()
    h.tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    expect(h.tray.get().map((i) => i.preview)).toEqual(['blob:1', 'blob:2']) // from the thumbnails
    expect(h.tray.uploading).toBe(true)
    h.uploads[1].resolve(image(8))
    await settle()
    expect(h.tray.ids()).toEqual([]) // its thumbnail isn't in yet
    h.thumbnails[0].resolve(image(8, 'image/webp'))
    await settle()
    expect(h.tray.ids()).toEqual([8])
    await uploaded(h, 0, 7)
    expect(h.tray.ids()).toEqual([7, 8]) // in the order they were added
    expect(h.tray.get()[0].attachment?.thumbnail).toBe('image/webp')
  })

  it('sends an image whose thumbnail failed, to be shown from the image itself', async () => {
    const h = harness()
    h.tray.add([new Blob(['a'])])
    await settle()
    h.uploads[0].resolve(image(3))
    await settle()
    h.thumbnails[0].reject(new ApiError('a thumbnail is up to 256 KB', 400))
    await settle()
    expect(h.tray.ids()).toEqual([3])
    expect(h.tray.get()[0].attachment?.thumbnail).toBeNull()
  })

  it('tries a dropped upload again, but not one the server refused', async () => {
    const h = harness()
    h.tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    h.uploads[0].reject(new TypeError('Failed to fetch'))
    h.uploads[1].reject(new ApiError("that isn't a JPEG, PNG or WebP image", 400))
    await settle()
    expect(h.waits).toEqual([1000])
    expect(h.uploads).toHaveLength(3) // the first, again
    await uploaded(h, 2, 5)
    expect(h.tray.ids()).toEqual([5])
    expect(h.tray.get()[1]).toMatchObject({ state: 'failed', retry: false, error: "that isn't a JPEG, PNG or WebP image" })
  })

  it('gives up after a few tries, and tries again when tapped', async () => {
    const h = harness()
    h.tray.add([new Blob(['a'])])
    for (let i = 0; i < 3; i++) {
      await settle()
      h.uploads[i].reject(new ApiError('Bad Gateway', 502)) // Sennet restarting behind its proxy
    }
    await settle()
    expect(h.waits).toEqual([1000, 3000])
    expect(h.tray.get()[0]).toMatchObject({ state: 'failed', retry: true })
    h.tray.retry(h.tray.get()[0].key)
    await settle()
    expect(h.steps.prepare).toHaveBeenCalledTimes(1) // made ready once
    await uploaded(h, 3, 9)
    expect(h.tray.ids()).toEqual([9])
  })

  it('forgets on the server an image taken out, even one still uploading', async () => {
    const h = harness()
    h.tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    await uploaded(h, 0, 1)
    const [first, second] = h.tray.get()
    h.tray.remove(first.key)
    expect(h.steps.remove).toHaveBeenCalledWith(1)
    h.tray.remove(second.key)
    h.uploads[1].resolve(image(2))
    await settle()
    expect(h.steps.remove).toHaveBeenCalledWith(2)
    expect(h.tray.get()).toEqual([])
  })

  it('lets go of what was sent, and keeps the rest', async () => {
    const h = harness()
    h.tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    await uploaded(h, 0, 1)
    await uploaded(h, 1, 2)
    h.tray.sent([1])
    expect(h.tray.ids()).toEqual([2])
    expect(h.steps.remove).not.toHaveBeenCalled() // it stays on the server with its message
  })

  it('says when an image can\'t be read', async () => {
    const h = harness()
    h.steps.prepare.mockRejectedValueOnce(new Error('decode failed'))
    h.tray.add([new Blob(['a'])])
    await settle()
    expect(h.tray.get()[0]).toMatchObject({ state: 'failed', retry: false, error: "Couldn't read that image" })
    expect(h.steps.upload).not.toHaveBeenCalled()
  })

  it(`takes up to ${MAX_IMAGES}`, () => {
    const h = harness()
    expect(h.tray.add(Array.from({ length: MAX_IMAGES + 2 }, () => new Blob(['x'])))).toBe(2)
    expect(h.tray.get()).toHaveLength(MAX_IMAGES)
  })
})

describe('shrinking', () => {
  it('scales the long side down and leaves a small image as it is', () => {
    expect(fitted(4032, 3024)).toEqual({ width: 1600, height: 1200 })
    expect(fitted(1170, 2532)).toEqual({ width: 739, height: 1600 })
    expect(fitted(800, 600)).toEqual({ width: 800, height: 600 })
    expect(fitted(4032, 3024, 480)).toEqual({ width: 480, height: 360 })
  })
})
