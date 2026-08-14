# Corporate TLS interception vs. ngrok

Notes from 2026-08-10. Relevant if you're on a managed laptop running Zscaler (or
any TLS-intercepting proxy) and ngrok won't connect. **If you deploy to a hosted
URL instead, none of this applies** — see the README's public-domain options.

## Status: temporarily bypassed, not fixed (2026-08-12)

IT granted a **temporary** proxy bypass on this machine, so `ngrok http 8000`
now connects and the demo runs locally against an `*.ngrok-free.app` domain.
Nothing below was solved — the bypass sidesteps it.

Treat this as borrowed. The grant can be revoked without notice, and it does not
transfer to any other machine or to a colleague following the README. The `twl`
dev box is no fallback — it's this machine's scratch deploy, not a sharing path,
and it can't reach Vertex (KNOWN-ISSUES #19). If ngrok starts failing again with
the x509 error below, assume the bypass lapsed before you debug anything else.

## Symptom

`ngrok http 8000` never establishes a tunnel and retries forever:

```
lvl=eror msg="failed to reconnect session" obj=tunnels.session
  err="failed to send authentication request: tls: failed to verify certificate:
       x509: certificate signed by unknown authority"
```

## Why

The proxy intercepts ngrok's tunnel endpoint. The certificate served for
`connect.ngrok-agent.com` is issued by the corporate CA, not ngrok's:

```
subject: CN=connect.ngrok-agent.com; O=Zscaler Inc.
issuer:  CN=Zscaler Intermediate Root CA (…); O=Zscaler Inc.
```

The corporate root **is** trusted system-wide, which is why `curl` and browsers
are fine (`ssl_verify_result=0`). The failure is specific to ngrok's Go binary,
which does not consult the macOS keychain for its tunnel connection.

Interception is **scoped, not global**. On this setup `*.twilio.com` is served its
genuine DigiCert certificate and is unaffected, which is why every Twilio API call
works without a workaround.

## Workaround attempts

| Attempt | Result |
|---|---|
| `SSL_CERT_FILE=<bundle exported from the system keychain>` | ❌ Ignored — still `x509: unknown authority`. ngrok pins its own CA pool. |
| `version: "3"` + top-level `root_cas: host` | ❌ Rejected: `field root_cas not found in type config.v3yamlConfig` |
| `version: "3"` + `agent: { root_cas: host }` | ❌ Rejected: not found in `config.Agent` |
| **`version: "2"` + `root_cas: host`** | ⚠️ **x509 error gone.** New error: `failed to send authentication request: session closed` |

The last row is the useful one — `root_cas: host` makes ngrok use the host trust
store, so the TLS layer is satisfied:

```yaml
# ngrok-corp.yml
version: "2"
root_cas: host
```

```bash
ngrok config add-authtoken <token>          # do this first
ngrok http 8000 --config=ngrok-corp.yml
```

**Unresolved:** whether the remaining `session closed` is simply the missing
authtoken — none was configured during any of these tests — or the proxy breaking
ngrok's multiplexed tunnel protocol after the handshake. **These cannot be
distinguished until an authtoken is added**, so add one and retry before drawing
conclusions.

If `session closed` persists with a valid authtoken, the tunnel protocol itself is
being broken. The options are a proxy bypass for `*.ngrok-agent.com` and
`*.ngrok.io`, or skipping ngrok and deploying to a hosted URL.

Note `root_cas: host` only changes which trust store ngrok validates against; it
does not make ngrok trust a pinned CA. Prefer asking IT for a bypass over relying
on it long-term.

## Unrelated diagnostic tip

`timeout` is **not** a stock macOS binary. A probe loop using it fails uniformly
and looks exactly like a network outage. Use `curl -m N` instead.
