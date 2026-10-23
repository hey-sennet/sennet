# M5: access

Status: done (steps 1 to 5); a real phone paired and worked in M6's clean install. Date: 2026-10-23.

M5 decides who may use a running Sennet, and how it is reached from a phone.
Packaging (`setup`, `service install`, release CI) follows in M6.

Until now access rested on Tailscale: the daemon binds loopback, `tailscale serve`
gives HTTPS, and an allowlist of Tailscale identities was planned (never built). That
leaves nothing for anyone hosting another way, and behind any proxy on the same machine
every request comes from 127.0.0.1, so "local" proves nothing. Sennet runs commands
as its user, so it needs its own answer to "may this request run?", whatever sits in
front of it.

## Decisions

- **Devices are paired, Jupyter-style.** There are no accounts or passwords. A device
  pairs once, with a one-time code, and keeps a token of its own in a cookie until it
  is revoked.
- **Every request is checked, loopback included.** A desktop browser on the same
  machine pairs too, by opening the link `sennet pair` prints. Nothing a proxy does
  can then open a back door. The one exception is `/mcp/<secret>`, which stays
  protected by its per-session secret (HLD §4.1).
- **Transport is the host's choice.** Tailscale stays the recommended route: it gives
  HTTPS and keeps Sennet off the internet. A reverse proxy (Caddy, nginx) or a
  tunnel works with nothing special, since pairing doesn't depend on what is in front.
  An authenticating proxy (Authelia, oauth2-proxy, Cloudflare Access) is an extra layer,
  not a replacement.
- **Not in v1:** trusting Tailscale's identity headers to pair the tailnet owner
  automatically, and passkeys. Both are open questions (HLD §12).

## Design

### Pairing

A pairing code is single use and short-lived. It comes in two forms:

- **A link**, `<url>/pair#<code>`, with a 128-bit code. The code is in the fragment, so
  it never reaches a proxy's logs; the pairing page posts it. Links come from
  `sennet pair` (valid 15 minutes), which also prints the link as a QR code in the
  terminal, and from Settings on a paired device (valid 5 minutes), as a QR code.
- **A typed code**, eight characters from an unambiguous alphabet (about 40 bits), shown
  beside the QR code for a phone that can't scan it. It is entered on the pairing screen
  any unpaired browser sees.

`sennet pair` writes the code to the database directly, so it works whether or not
the daemon is running, and needs no login: anyone who can run it is already the user.
It is also the recovery path when every device is lost.

A link needs the address the phone will use, which a desktop on `localhost` doesn't
know. `server.public_url` in the config holds it; `setup` fills it from
`tailscale status` when Tailscale is installed. Without it, the link uses the address
the request came in on, and the typed code is the way to pair a phone.

Redeeming a code creates a device and sets its cookie. Wrong codes are rate-limited
across all clients (10 a minute, then a pause), which, with codes that expire in minutes,
keeps the typed code safe even on a public address.

### Devices

Each device has a 256-bit random token. The cookie (`sennet_device`) is HttpOnly,
SameSite=Strict, Secure when the browser is on HTTPS (the request's scheme, or its Origin
behind a proxy that doesn't say), and lasts a year. The
database stores only a SHA-256 hash of the token, so a copy of the database can't be
used to sign in.

A device has a name (from its user agent, such as "Firefox on Linux", which can be
renamed), when it paired, when it was last seen, and how it paired (`cli` or the
device that showed the code). Settings lists them, and revokes any of them; revoking
also closes that device's open sockets. `sennet devices` lists and revokes from the
terminal.

### Checks

- Unpaired requests to `/api/...` get 401. WebSockets close with code 4401, which the
  client takes as "pair this device" rather than "reconnect".
- The app shell and its assets load without a cookie, so an unpaired browser can show
  the pairing screen.
- The `X-Sennet` header (CSRF) and the WebSocket Origin check stay as they are.
- Responses get a Content-Security-Policy (`default-src 'self'`, no inline scripts, no
  framing; inline styles are allowed, since UI libraries inject them), `nosniff` and
  `no-referrer`: agent output is rendered as markdown, and a stolen cookie is a remote
  shell. Images in agent output load only from Sennet itself.

### Hosting

- **Tailscale:** `tailscale serve --bg 7710`. The guide covers setup end to end.
- **Reverse proxy:** the daemon still binds loopback; the proxy terminates HTTPS and
  must pass WebSocket upgrades. The guide gives Caddy and nginx examples.
- **Public exposure** (Tailscale Funnel, an open port) works but is warned against in
  the guide and by `doctor`, which reports when Funnel is on.
- Binding a non-loopback address directly stays a warning: without HTTPS the
  microphone doesn't work anyway.

## Steps

### 1. Storage and CLI

Done (421bc56, with step 2). Schema version 6: `devices` (name, token hash, how it paired, paired and last-seen
times) and `pairing_codes` (code hash, form, expiry, who made it). `sennet pair`
prints a link and a terminal QR code; `sennet devices` lists and revokes. QR codes
come from `segno` (BSD), used for the terminal and for Settings' SVG. `sennet serve`
also prints a pairing link while no device is paired, as Jupyter prints its token.

### 2. The check

Done (421bc56). Middleware on `/api/...` and both WebSockets, `POST /api/pair` to redeem a code (exempt
from the check, rate-limited), and the security headers. Tests get a paired-client
fixture; new tests cover unpaired requests, revocation closing sockets, expiry, reuse
and rate limits, and that loopback isn't trusted. Revoking a device from the terminal
reaches the daemon only through the database, so open sockets are checked every 30 s and
closed when their device is gone.

### 3. Client

Done (8b538f5). The pairing screen (link or typed code), handling 401 and 4401, and Settings: the
devices list (rename, revoke) and "Pair a device", with the QR code and the typed code.
The app asks `GET /api/devices` before it renders, so an unpaired browser goes straight to
the pairing screen. A `devices` event on `/api/events` keeps Settings current, and closes
the code once another device has paired with it. Without a public address, a code shown on
a loopback page has no QR code (its link would only work on this computer), just the code
to type. Browser tests start paired, from a device the e2e server adds, and a suite of
their own pairs a fresh browser by code and by link.

### 4. Public URL

Done (9d377c0). `server.public_url`, filled by `setup` from Tailscale and checked by `doctor`; `doctor`
also reports Funnel, as a problem. Setup asks before using the tailnet address, keeps an
address set by hand, and says to run `tailscale serve --bg 7710` when nothing serves it
yet. Tailscale isn't installed on the development machine, so `tailscale.py` is tested
only against hand-written JSON in the shape of its `status` and `serve status` output.

### 5. Docs

Done. HLD §2, §7, §8, §9, §10 and §12, voice-protocol §1, the README, and
[hosting.md](hosting.md) for Tailscale, Caddy and nginx.
