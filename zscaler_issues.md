# Zscaler issues

Found 2026-08-10 while configuring this demo's Twilio account. Cause was
verified as Zscaler before writing anything down — see "How it was confirmed".

## TL;DR

| Thing | Status |
|---|---|
| Twilio APIs (api / knowledge / conversations / memory / studio) | ✅ Unaffected — Zscaler bypasses Twilio |
| OpenAI API | ✅ Reachable, TLS verifies clean |
| **ngrok tunnel** | 🔴 **Blocked** — Zscaler MITM breaks the agent's TLS |
| Twilio account provisioning for this demo | ✅ Went ahead, not blocked by this |

Only `TWILIO_VOICE_PUBLIC_DOMAIN` in `.env` is left unset because of this.

## The problem

`ngrok http 8000` never establishes a tunnel. It retries forever with:

```
lvl=eror msg="failed to reconnect session" obj=tunnels.session
  err="failed to send authentication request: tls: failed to verify certificate:
       x509: certificate signed by unknown authority"
```

## How it was confirmed as Zscaler

1. **Zscaler is intercepting the ngrok endpoint.** The cert served for
   `connect.ngrok-agent.com` is issued by Zscaler, not by ngrok's real CA:

   ```
   subject: CN=connect.ngrok-agent.com; O=Zscaler Inc.; OU=Zscaler Inc.
   issuer:  CN=Zscaler Intermediate Root CA (zscalertwo.net); O=Zscaler Inc.
   ```

2. **Twilio is *not* intercepted** — genuine issuer, which matches
   "Twilio URLs always work":

   ```
   subject: O=Twilio Inc.; CN=*.twilio.com
   issuer:  O=DigiCert Inc; CN=DigiCert Global G2 TLS RSA SHA256 2020 CA1
   ```

3. **The Zscaler root IS trusted system-wide** (1 match in
   `/Library/Keychains/System.keychain`), which is why `curl` reports
   `SSL certificate verify ok` / `ssl_verify_result=0` for every host, and why
   browsers are fine. The failure is specific to ngrok's Go binary, which does
   not consult the macOS keychain for its tunnel connection.

So: interception is real, it is Zscaler, and it is scoped to non-Twilio hosts.

## Workaround progress (partial — not solved)

| Attempt | Result |
|---|---|
| `SSL_CERT_FILE=<171-cert bundle exported from System keychain>` | ❌ Ignored. Still `x509: unknown authority`. ngrok pins its own CA pool. |
| `version: "3"` + top-level `root_cas: host` | ❌ Rejected: `field root_cas not found in type config.v3yamlConfig` |
| `version: "3"` + `agent: { root_cas: host }` | ❌ Rejected: `field root_cas not found in type config.Agent` |
| **`version: "2"` + `root_cas: host`** | ⚠️ **x509 error GONE.** New error: `failed to send authentication request: session closed` |

The last row is the useful one. `root_cas: host` makes ngrok use the host trust
store, so it now accepts the Zscaler cert — the TLS layer is fixed. Config that
got that far:

```yaml
# /tmp/ngrok_v2b.yml
version: "2"
root_cas: host
```

```bash
ngrok http 8000 --config=/tmp/ngrok_v2b.yml
```

**Unresolved:** whether `session closed` is (a) just the missing authtoken —
no authtoken was configured during any of these tests, there is no
`~/Library/Application Support/ngrok/ngrok.yml` — or (b) Zscaler's proxy
breaking ngrok's multiplexed tunnel protocol after the handshake. **These two
cannot be distinguished until an authtoken is added.** Do that first:

```bash
ngrok config add-authtoken <token>   # then retry with the version:2 config above
```

If `session closed` persists with a valid authtoken, the tunnel protocol itself
is being broken and the options are a Zscaler bypass/PAC exception for
`*.ngrok-agent.com` + `*.ngrok.io`, or skip ngrok entirely (see below).

Also note `root_cas: host` only silences cert validation against the host store
— it does **not** make ngrok trust a pinned ngrok CA. Ask IT for a bypass rather
than relying on this long-term.

## Alternative that avoids ngrok completely

This machine already has a working public HTTPS ingress: the `twl` dev box,
serving `*.twl.dtolb.com` (`twl list` shows `flight-sandbox`,
`aci-quality-poc`, `hello` all running). Deploying this demo there would give a
**stable** domain, e.g. `gd-poc.twl.dtolb.com`, for
`TWILIO_VOICE_PUBLIC_DOMAIN` — with no ngrok, no Zscaler exposure, and no
re-editing `.env` every restart.

Caveats to check before committing to it: needs a Dockerfile, and Traefik must
pass through the ConversationRelay **websocket** (TAC needs `wss://`). Inbound
traffic is Twilio → your box, so Zscaler on this laptop is not in that path.

## Unrelated credential trap found at the same time

Not Zscaler, but it will produce confusing 401s. Recorded here so it isn't
mistaken for a proxy problem:

- Shell exports `TWILIO_API_KEY=SKf5c81c…`, a **restricted key with no
  permissions** (every call returns `70051 Authorization Error`).
- `.env` has the good key `SKee7879…` (full permissions, verified).
- `app.py:47` calls `load_dotenv()` without `override=True`, so **the shell key
  wins** and every Twilio call fails.

Fix: `load_dotenv(override=True)`, and `unset TWILIO_API_KEY TWILIO_API_SECRET`
in the shell.
