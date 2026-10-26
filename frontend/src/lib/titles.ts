// A task's title is its first message until the agent writes one (src/sennet/titles.py), less the
// fillers speech recognition keeps. These are that file's fillers; keep the two in step.
const FILLER = /^(?:u+m+|u+h+m*|e+r+m*|h+m+)$/
const LEADING = /^(?:u+m+|u+h+m*|e+r+m*|h+m+|okay|ok|so|well|alright|yeah)$/

const words = (text: string) => text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean)

/**
 * Whether a message says no more than the task's title: the title was made from it, whole.
 * A title cut short, or one the agent wrote, leaves the message something to add.
 */
export function repeatsTitle(title: string, text: string): boolean {
  if (title.endsWith('…')) return false
  const said = words(text)
  while (said.length && LEADING.test(said[0])) said.shift()
  const kept = said.filter((word) => !FILLER.test(word))
  return kept.length > 0 && kept.join(' ') === words(title).join(' ')
}
