import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api, type Device, type Pairing, type ServerEvent } from '@/lib/api'
import { relativeTime } from './TaskList'

/** Settings' paired devices: rename or unpair them, and show a code to pair another. Mounted while
 * Settings is open. */
export function Devices({ subscribe }: {
  subscribe: (listener: (event: ServerEvent) => void) => () => void
}) {
  const [devices, setDevices] = useState<Device[] | null>(null)
  const [pairing, setPairing] = useState<Pairing | null>(null)

  const load = useCallback(() => api.devices().then(setDevices, () => {}), [])
  useEffect(() => {
    void load()
    return subscribe((event) => {
      if (event.type !== 'devices') return
      void load()
      if (event.paired) {
        setPairing(null)
        toast.success(`Paired ${event.paired.name}`)
      }
    })
  }, [subscribe, load])

  const revoke = async (device: Device) => {
    const question = device.current
      ? 'Unpair this device? You will need a new code to use Agent Talk here again.'
      : `Unpair ${device.name}? It will need a new code to get back in.`
    if (!confirm(question)) return
    try {
      await api.revokeDevice(device.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not unpair the device')
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {!devices && <p className="text-sm text-muted-foreground">Loading…</p>}
      {devices?.map((device) => (
        // Keyed by name too, so a rename made elsewhere replaces what is typed here.
        <DeviceRow key={`${device.id}:${device.name}`} device={device} onRevoke={() => revoke(device)} />
      ))}
      {pairing ? <ShowPairing pairing={pairing} onDone={() => setPairing(null)} /> : (
        <Button variant="outline" className="self-start" onClick={() => api.newPairing().then(setPairing, (error) =>
          toast.error(error instanceof Error ? error.message : 'Could not make a pairing code'))}>
          Pair a device
        </Button>
      )}
    </div>
  )
}

function DeviceRow({ device, onRevoke }: { device: Device; onRevoke: () => void }) {
  const [name, setName] = useState(device.name)
  const save = async () => {
    if (name.trim() === device.name) return
    try {
      await api.renameDevice(device.id, name)
    } catch (error) {
      setName(device.name)
      toast.error(error instanceof Error ? error.message : 'Could not rename the device')
    }
  }
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Input value={name} onChange={(event) => setName(event.target.value)} onBlur={save}
            onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
            aria-label="Device name" className="h-7 border-transparent px-1 font-medium dark:bg-transparent hover:border-input" />
          {device.current && <span className="shrink-0 text-xs text-muted-foreground">This device</span>}
        </div>
        <p className="px-1 text-xs text-muted-foreground">
          Paired by {device.paired_by} {relativeTime(device.created_at)}
          {!device.current && <> · last seen {relativeTime(device.last_seen_at)}</>}
        </p>
      </div>
      <Button variant="ghost" size="icon-sm" aria-label={`Unpair ${device.name}`} onClick={onRevoke}>
        <X />
      </Button>
    </div>
  )
}

function ShowPairing({ pairing, onDone }: { pairing: Pairing; onDone: () => void }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [])
  const left = Math.max(0, Math.round(pairing.expires_at - now))
  useEffect(() => {
    if (left === 0) onDone()
  }, [left, onDone])

  return (
    <div className="flex flex-col gap-3 rounded-lg border px-3 py-3 sm:flex-row sm:items-center sm:gap-5">
      {!pairing.local_only && (
        <img src={pairing.qr} alt="QR code to pair a device" className="size-40 shrink-0 rounded-md" />
      )}
      <div className="flex min-w-0 flex-col gap-2 text-sm">
        <p className="text-muted-foreground">
          {pairing.local_only
            ? 'On the other device, open Agent Talk at the address you use there and type this code:'
            : 'Scan the code with the other device, or open Agent Talk there and type:'}
        </p>
        <p className="font-mono text-2xl tracking-[0.2em]" aria-label="Pairing code">{pairing.code}</p>
        <p className="text-xs text-muted-foreground">
          Works once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}.
          {pairing.local_only && ' To get a QR code for your phone, set server.public_url in the config, or run agent-talk setup.'}
        </p>
        <Button variant="ghost" size="sm" className="self-start text-muted-foreground" onClick={onDone}>Done</Button>
      </div>
    </div>
  )
}
