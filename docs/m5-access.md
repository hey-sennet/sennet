# M5: access

Status: planned. Date: 2026-10-23.

M5 decides who may use a running Agent Talk, and how it is reached from a phone.
Packaging (`setup`, `service install`, release CI) follows in M6.

Until now access rested on Tailscale: the daemon binds loopback, `tailscale serve`
gives HTTPS, and an allowlist of Tailscale identities was planned (never built). That
leaves nothing for anyone hosting another way, and behind any proxy on the same machine
every request comes from 127.0.0.1, so "local" proves nothing. Agent Talk runs commands
as its user, so it needs its own answer to "may this request run?", whatever sits in
front of it.

## Decisions

- **Devices are paired, Jupyter-style.** There are no accounts or passwords. A device
  pairs once, with a one-time code, and keeps a token of its own in a cookie until it
  is revoked.
- **Every request is checked, loopback included.** A desktop browser on the same
  machine pairs too, by opening the link `agent-talk pair` prints. Nothing a proxy does
  can then open a back door. The one exception is `/mcp/<secret>`, which stays
  protected by its per-session secret (HLD §4.1).
- **Transport is the host's choice.** Tailscale stays the recommended route: it gives
  HTTPS and keeps Agent Talk off the internet. A reverse proxy (Caddy, nginx) or a
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
  `agent-talk pair` (valid 15 minutes), which also prints the link as a QR code in the
  terminal, and from Settings on a paired device (valid 5 minutes), as a QR code.
- **A typed code**, eight characters from an unambiguous alphabet (about 40 bits), shown
  beside the QR code for a phone that can't scan it. It is entered on the pairing screen
  any unpaired browser sees.

`agent-talk pair` writes the code to the database directly, so it works whether or not
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

Each device has a 256-bit random token. The cookie (`agent_talk_device`) is HttpOnly,
SameSite=Strict and Secure, except over plain HTTP on loopback, and lasts a year. The
database stores only a SHA-256 hash of the token, so a copy of the database can't be
used to sign in.

A device has a name (from its user agent, such as "Firefox on Linux", which can be
renamed), when it paired, when it was last seen, and how it paired (`cli` or the
device that showed the code). Settings lists them, and revokes any of them; revoking
also closes that device's open sockets. `agent-talk devices` lists and revokes from the
terminal.

### Checks

- Unpaired requests to `/api/...` get 401. WebSockets close with code 4401, which the
  client takes as "pair this device" rather than "reconnect".
- The app shell and its assets load without a cookie, so an unpaired browser can show
  the pairing screen.
- The `X-Agent-Talk` header (CSRF) and the WebSocket Origin check stay as they are.
- Responses get a Content-Security-Policy (`default-src 'self'`, no inline scripts) and
  `X-Content-Type-Options`, `Referrer-Policy` and `frame-ancestors 'none'`: agent
  output is rendered as markdown, and a stolen cookie is a remote shell.

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

Schema version 6: `devices` (name, token hash, how it paired, paired and last-seen
times) and `pairing_codes` (code hash, form, expiry, who made it). `agent-talk pair`
prints a link and a terminal QR code; `agent-talk devices` lists and revokes. QR codes
come from `segno` (BSD), used for the terminal and for Settings' SVG.

### 2. The check

Middleware on `/api/...` and both WebSockets, `POST /api/pair` to redeem a code (exempt
from the check, rate-limited), and the security headers. Tests get a paired-client
fixture; new tests cover unpaired requests, revocation closing sockets, expiry, reuse
and rate limits, and that loopback isn't trusted.

### 3. Client

The pairing screen (link or typed code), handling 401 and 4401, and Settings: the
devices list (rename, revoke) and "Pair a device", with the QR code and the typed code.

### 4. Public URL

`server.public_url`, filled by `setup` from Tailscale and checked by `doctor`; `doctor`
also reports Funnel.

### 5. Docs

HLD §2, §7, §8, §9, §10 and §12, voice-protocol §1, the README, and guides for
Tailscale, Caddy and nginx.
