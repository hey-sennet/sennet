# Reaching Agent Talk from your phone

Agent Talk listens on `127.0.0.1:7710` only. A phone reaches it through something in front
that gives it an HTTPS address, because browsers only let a page use the microphone over
HTTPS (or on `localhost`). Tailscale is the simplest; any reverse proxy works.

Whatever is in front, every browser pairs before it can use Agent Talk, including one on
the same computer ([m5-access.md](m5-access.md)). A paired device can run commands on your
computer as you, so unpair any you stop using: in Settings, or with `agent-talk devices`.

## Pairing

```sh
agent-talk pair
```

prints a link for this computer, a link and QR code for your phone (when
`server.public_url` is set), and a code to type. Each works once, for 15 minutes. Open the
link, scan the QR code, or open Agent Talk on the other device and type the code.

A paired device can pair another from Settings, under Devices: "Pair a device" shows a QR
code and a code to type, for 5 minutes. If you lose every device, `agent-talk pair` gets
you back in.

## Tailscale (recommended)

[Tailscale](https://tailscale.com) puts your computer and phone on a private network and
gives the computer an HTTPS address on it. Nothing is exposed to the internet.

1. Install Tailscale on the computer and the phone, and sign in to the same tailnet on
   both. In the tailnet's admin console, turn on MagicDNS and HTTPS certificates.
2. Run `agent-talk setup`. It finds the computer's address, such as
   `https://desk.tail1234.ts.net`, and saves it as `server.public_url`.
3. Setup then offers to pass that address on to Agent Talk, which is:

   ```sh
   tailscale serve --bg 7710
   ```

   The first time on a tailnet, it prints a link to turn HTTPS on and waits. On Linux,
   if it says access is denied, let your user change Tailscale's settings with
   `sudo tailscale set --operator=$USER` and run it again. It replaces whatever this
   computer already serves at `/` on its tailnet address; setup warns when there is
   something.
4. Setup ends by printing a pairing link and QR code (or run `agent-talk pair`); scan it
   with the phone.

`agent-talk doctor` checks each of these.

## Caddy

Caddy gets a certificate for your domain by itself, passes WebSockets through, and keeps
the `Host` header, which Agent Talk checks WebSockets against.

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

Tailscale Funnel or an open port puts Agent Talk on the internet. Pairing still guards it:
codes work once, expire in minutes, and wrong ones are throttled. But a paired device is a
shell on your computer, so keep Agent Talk on a private network unless you have a reason
not to. `agent-talk doctor` reports when Funnel is on.
