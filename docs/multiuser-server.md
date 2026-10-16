# Multi-user server

Status: implemented and reviewed, never yet deployed — see [`docs/superpowers/specs/2026-10-14-multiuser-webapp-handoff.md`](superpowers/specs/2026-10-14-multiuser-webapp-handoff.md) for what a deployment must verify.

`gee-animation-server` hosts the same Gradio app for many people at once, behind Google
sign-in. Each person authorises Earth Engine with their own Google account and renders
against their own Cloud project; renders run as isolated worker subprocesses, queued
behind a shared cap. The stack in `deploy/` (app + Caddy for TLS) is meant for
`carrot.aisl.c4ds.hnee.de`, which is reachable only from the HNEE VPN.

**Prerequisites**

1. A certificate browsers trust. Let's Encrypt HTTP-01 cannot reach a VPN-only host. The
   working default: obtain a cert and key for `carrot.aisl.c4ds.hnee.de` from the HNEE
   internal CA (or any browser-trusted source), put them in `deploy/` as `carrot.crt` and
   `carrot.key`, and uncomment the two matching volume lines in `deploy/compose.yaml`.
   For automated renewal instead, use DNS-01: build Caddy with your DNS provider's plugin
   via `xcaddy` (the stock `caddy:2` image cannot), put `DNS_API_TOKEN` in `deploy/.env`,
   and switch the `tls` line in `deploy/Caddyfile` as its comments describe.
2. A Google OAuth client whose consent screen is **Internal**, or External in testing
   mode with `hnee.de` verified and at most 100 named test users. Its authorised redirect
   URI is `https://carrot.aisl.c4ds.hnee.de/oauth2/callback`.

**Per-user setup.** Every user needs their own Google Cloud project with the Earth Engine
API enabled and registered for Earth Engine. The project field in the UI starts
**empty**; each person types their own project id there. Nothing is billed to HNEE's
project.

**Deploy**

```bash
cd deploy
cp .env.example .env && chmod 600 .env   # fill in the OAuth client and cookie secret
docker compose up -d --build
```

The app reads `GEE_OAUTH_CLIENT_ID`, `GEE_OAUTH_CLIENT_SECRET`, `GEE_OAUTH_REDIRECT_URI`
(set by `compose.yaml`) and `GEE_COOKIE_SECRET`, all required, plus the optional
`GEE_OUTPUT_DIR`, `GEE_ANIMATION_CACHE_DIR` and `GEE_FORWARDED_ALLOW_IPS`. `deploy/.env`
is git-ignored; never commit it. `EE_PROJECT` (set in the image for the single-user
container) has no effect in server mode, because each user supplies their own project.

**Limits** (defaults in `AppConfig`): at most 3 renders run at once (others wait in the
queue), uploads up to 100 MB, an animation spans at most 12 years and at most 1440p, and
every run directory (finished or not) is deleted after 14 days. Renders are long (a
ten-year monthly animation can take tens of minutes to hours), so one render may run for
up to 4 hours (`GEE_JOB_TIMEOUT_S`, default 14400) before its worker is killed, and the
Caddyfile allows matching 4-hour proxy timeouts. Gradio's copies of returned files live in
`GRADIO_TEMP_DIR` (`/data/cache/gradio-tmp`, set by the compose service — not by the image,
which also serves the single-operator GUI, where nothing would reclaim them) and are swept
on the same schedule. A sign-in lasts 7 days; **Stop all my renders** cancels your own
queued and running renders — all of them, and only yours — and frees the slots at once.
The compose service has a healthcheck but **no memory limit**: a ceiling below a render's
real peak would OOM-kill it hours in, which is worse than no ceiling, so `mem_limit` is
left commented out in `deploy/compose.yaml` with a note on sizing it from an observed run.

> Renders run as subprocesses on a shared machine. Anyone with Docker access on carrot
> can read a running worker's memory, which briefly holds your Google refresh token.
> Use the server only if you accept that; no credential is ever written to disk
> (uploaded AOIs and finished renders are, until the retention sweep removes them).

