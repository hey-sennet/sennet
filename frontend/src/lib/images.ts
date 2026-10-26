// Images are shrunk on the device before they're sent (src/sennet/attachments.py): a phone's
// photo is several MB, and the agent reads a long side of about 1600 px just as well. Each is
// decoded once, and drawn twice: at that size for the agent, and small for the timeline.

export const LONG_SIDE = 1600
export const THUMBNAIL_SIDE = 480 // shown up to 240 px wide in the timeline, at twice the pixels
const QUALITY = 0.85
const THUMBNAIL_QUALITY = 0.75
// A screenshot stays a PNG, which keeps text sharp, unless that comes out bigger than this.
const PNG_LIMIT = 1.5 * 1024 * 1024

export interface Prepared {
  image: Blob
  thumbnail: Blob
}

/** The size to draw an image at: its own, or scaled down to `longSide` on its longer side. */
export function fitted(width: number, height: number, longSide = LONG_SIDE): { width: number; height: number } {
  const scale = Math.min(1, longSide / Math.max(width, height))
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) }
}

/**
 * The image to send and its thumbnail, from a file or a camera frame (which is drawn straight from
 * the camera, never saved in between). A PNG stays a PNG when that stays small; anything else is a
 * WebP where the browser can write one (about a third smaller than a JPEG that looks the same),
 * else a JPEG. Drawing it afresh also leaves out what a file carried besides the picture, such as
 * where a photo was taken.
 */
export async function prepare(source: Blob | ImageBitmap): Promise<Prepared> {
  const bitmap = source instanceof Blob ? await createImageBitmap(source, { imageOrientation: 'from-image' }) : source
  try {
    const lossy = await lossyType()
    const png = source instanceof Blob && source.type === 'image/png'
    let image = png ? await draw(bitmap, LONG_SIDE, 'image/png') : null
    if (!image || image.size > PNG_LIMIT) image = await draw(bitmap, LONG_SIDE, lossy, QUALITY)
    return { image, thumbnail: await draw(bitmap, THUMBNAIL_SIDE, lossy, THUMBNAIL_QUALITY) }
  } finally {
    bitmap.close()
  }
}

/** The bitmap at `longSide`, encoded. Lossy formats get a white background, as JPEG has no transparency. */
async function draw(bitmap: ImageBitmap, longSide: number, type: string, quality?: number): Promise<Blob> {
  const size = fitted(bitmap.width, bitmap.height, longSide)
  const canvas = canvasOf(size.width, size.height)
  const context = canvas.getContext('2d') as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D
  context.imageSmoothingQuality = 'high'
  if (type !== 'image/png') {
    context.fillStyle = '#fff'
    context.fillRect(0, 0, size.width, size.height)
  }
  context.drawImage(bitmap, 0, 0, size.width, size.height)
  return encode(canvas, type, quality)
}

function canvasOf(width: number, height: number): HTMLCanvasElement | OffscreenCanvas {
  // An OffscreenCanvas encodes without holding up the page, where there is one.
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  return canvas
}

function encode(canvas: HTMLCanvasElement | OffscreenCanvas, type: string, quality?: number): Promise<Blob> {
  if (canvas instanceof HTMLCanvasElement) {
    return new Promise((resolve, reject) =>
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Couldn't read that image"))), type, quality))
  }
  return canvas.convertToBlob({ type, quality })
}

let lossy: Promise<string> | null = null

/** WebP if this browser writes it (Safari quietly writes a PNG instead), else JPEG. Asked once. */
function lossyType(): Promise<string> {
  lossy ??= (() => {
    const probe = canvasOf(2, 2)
    probe.getContext('2d') // a canvas without a context can't be encoded
    return encode(probe, 'image/webp', 0.5).then(
      (blob) => (blob.type === 'image/webp' ? 'image/webp' : 'image/jpeg'), () => 'image/jpeg')
  })()
  return lossy
}
