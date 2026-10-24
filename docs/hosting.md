# Reaching Sennet from your phone

Sennet listens on `127.0.0.1:7710` only. A phone reaches it through something in front
that gives it an HTTPS address, because browsers only let a page use the microphone over
HTTPS (or on `localhost`). Tailscale is the simplest; any reverse proxy works.

Whatever is in front, every browser pairs before it can use Sennet, including one on
the same computer ([m5-access.md](m5-access.md)). A paired device can run commands on your
computer as you, so unpair any you stop using: in Settings, or with `sennet devices`.

## Pairing

```sh
sennet pair
```

prints a link for this computer, a link and QR code for your phone (when
`server.public_url` is set), and a code to type. Each works once, for 15 minutes. Open the
link, scan the QR code, or open Sennet on the other device and type the code.

A paired device can pair another from Settings, under Devices: "Pair a device" shows a QR
code and a code to type, for 5 minutes. If you lose every device, `sennet pair` gets
you back in.

## Tailscale (recommended)

[Tailscale](https://tailscale.com) puts your computer and phone on a private network and
gives the computer an HTTPS address on it. Nothing is exposed to the internet.

1. Install Tailscale on the computer and the phone, and sign in to the same tailnet on
   both. In the tailnet's admin console, turn on MagicDNS and HTTPS certificates.
2. Run `sennet setup`. It finds the computer's address, such as
   `https://desk.tail1234.ts.net`, and saves it as `server.public_url`.
3. Setup then offers to pass that address on to Sennet, which is:

   ```sh
   tailscale serve --bg 7710
   ```

   The first time on a tailnet, it prints a link to turn HTTPS on and waits. On Linux,
   if it says access is denied, let your user change Tailscale's settings with
   `sudo tailscale set --operator=$USER` and run it again. It replaces whatever this
   computer already serves at `/` on its tailnet address; setup warns when there is
   something.
4. Setup ends by printing a pairing link and QR code (or run `sennet pair`); scan it
   with the phone.

`sennet doctor` checks each of these.

To set it up by hand instead, or after the fact:

```sh
tailscale status                   # this computer is on the tailnet
tailscale serve --bg 7710          # pass its HTTPS address on to Sennet
tailscale serve status             # shows https://<machine>.<tailnet>.ts.net → 127.0.0.1:7710
```

then put that address in `config.toml` as `server.public_url` (see Caddy below), and run
`sennet service restart` and `sennet pair`.

On the phone, open the address in Chrome (Android) or Safari (iOS). Add it to the home
screen to open Sennet like an app. Safari hasn't been tested with conversations yet;
push-to-talk is the safer choice there.

### When the phone can't reach Sennet

- **The page doesn't load.** Check the phone is on the tailnet: the Tailscale app is on
  and signed in to the same account or tailnet as the computer. Then check
  `tailscale serve status` on the computer still lists port 7710, and
  `sennet service status` says Sennet answers on it.
- **A certificate error, or `tailscale serve` waits forever.** HTTPS certificates aren't
  turned on for the tailnet. Turn on MagicDNS and HTTPS in the admin console's DNS page,
  then run `tailscale serve --bg 7710` again. The first load of a new address can take a
  few seconds while the certificate is issued.
- **"Access denied" from `tailscale serve`** (Linux). Run
  `sudo tailscale set --operator=$USER` once, then try again.
- **It loads, but asks to pair.** Every browser pairs once. Run `sennet pair` and scan
  the QR code, or type the code. If the QR code is missing, `server.public_url` isn't
  set.
- **It loads, but the mic doesn't work.** The address must be `https://`; browsers
  block the microphone on plain `http://` everywhere but `localhost`. Check the site's
  microphone permission in the browser's settings.
- **Voice stops when the phone locks or you switch apps.** Mobile browsers cut a
  background page's microphone. Keep Sennet in front while you talk.
- **It worked, then stopped after you logged out** (Linux). The service stops at logout
  unless you run `loginctl enable-linger`.

## Caddy

Caddy gets a certificate for your domain by itself, passes WebSockets through, and keeps
the `Host` header, which Sennet checks WebSockets against.

```caddy
agent.example.com {
    reverse_proxy 127.0.0.1:7710
}
```

Then set the address pairing links use, in `config.toml`:

```toml
[server]
public_url = "https://agent.example.com"
```

## nginx

nginx needs to be told to pass WebSocket upgrades and the original `Host`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name agent.example.com;
    ssl_certificate     /etc/letsencrypt/live/agent.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agent.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7710;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 1h;   # the event and voice sockets stay open
    }
}
```

and set `server.public_url` as for Caddy.

## A login in front

A proxy with its own login, such as Authelia, oauth2-proxy or Cloudflare Access, adds a
layer in front of pairing; it doesn't replace it.

The proxy never needs to serve `/mcp/`: only agents on this computer call it, straight to
`127.0.0.1:7710`, each with a secret of its own. You may block it at the proxy.

## On the public internet

Tailscale Funnel or an open port puts Sennet on the internet. Pairing still guards it:
codes work once, expire in minutes, and wrong ones are throttled. But a paired device is a
shell on your computer, so keep Sennet on a private network unless you have a reason
not to. `sennet doctor` reports when Funnel is on.
