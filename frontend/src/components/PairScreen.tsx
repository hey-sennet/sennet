import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'

/**
 * What an unpaired browser sees (docs/m5-access.md). A pairing link (`/pair#<code>`) pairs it
 * straight away; otherwise the code from `sennet pair`, or from Settings on a paired device,
 * is typed here. Pairing reloads the app, now with this device's cookie.
 */
export function PairScreen() {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fromLink = useRef(location.pathname === '/pair' ? location.hash.slice(1) : '')

  const pair = async (value: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.pair(value)
      location.replace('/')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not pair this device')
      setBusy(false)
    }
  }

  useEffect(() => {
    // The code is used once; keep it out of the address bar and history either way.
    if (location.pathname === '/pair') history.replaceState(null, '', '/')
    const linked = fromLink.current
    fromLink.current = ''
    if (linked) void pair(linked)
  }, [])

  return (
    <main className="grid min-h-dvh place-items-center px-6">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <p className="font-serif text-[22px]">sennet<span className="text-muted-foreground">.</span></p>
        <div className="flex flex-col gap-2">
          <h1 className="font-serif text-3xl">Pair this device</h1>
          <p className="text-sm text-muted-foreground">
            Sennet runs commands on your computer, so each browser pairs once before it can use it.
            Run <code className="font-mono text-xs text-foreground">sennet pair</code> on that computer and
            open the link it prints, or type the code it shows. A paired device can show a code too, in Settings.
          </p>
        </div>
        <form className="flex flex-col gap-3" onSubmit={(event) => {
          event.preventDefault()
          if (code.trim()) void pair(code)
        }}>
          <Input value={code} onChange={(event) => setCode(event.target.value)} placeholder="XXXX-XXXX"
            aria-label="Pairing code" autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false}
            className="h-11 text-center font-mono text-lg tracking-[0.2em] md:text-lg" disabled={busy} />
          <Button type="submit" size="lg" disabled={busy || !code.trim()}>{busy ? 'Pairing…' : 'Pair'}</Button>
          {error && <p role="alert" className="text-sm text-warn">{error[0].toUpperCase() + error.slice(1)}.</p>}
        </form>
      </div>
    </main>
  )
}
