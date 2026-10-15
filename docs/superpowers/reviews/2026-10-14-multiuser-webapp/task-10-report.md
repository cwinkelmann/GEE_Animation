# Task 10 report

Status: DONE_WITH_CONCERNS. Commits: dd92642 (deploy files, Dockerfile, .gitignore), 39905f5 (README).

Tests: 839 passed, 1 skipped, 2 deselected, 1 warning (baseline unchanged). test_gui.py untouched.

Verified: compose.yaml parses as YAML (services app, caddy); every GEE_* variable the code reads
is in .env.example or set by compose/Dockerfile; /oauth2/callback is a real route; cap numbers
(3, 100 MB, 14 days, 12 years, 1440p) match AppConfig and caps.py; deploy/.env was NOT ignored,
so I added it to .gitignore and `git check-ignore -v` now prints the rule.

NOT verified: Docker CLI exists but the daemon is not running, so `docker compose config`,
`docker build` and `docker run` were NOT run. The Caddyfile was not validated.

Concerns:
1. Caddyfile keeps the brief's literal `dns <provider>` placeholder; it will not parse until
   replaced, and stock caddy:2 has no DNS plugin (README says so).
2. I added `DNS_API_TOKEN: ${DNS_API_TOKEN:-}` to the caddy service so the token reaches Caddy.
   The app also receives it through env_file (harmless, unused).
3. Dockerfile now runs as user app, so I changed the README's single-user mount
   from /root/.config to /home/app/.config.
4. Image keeps ENV EE_PROJECT=hnee-331218; webapp code never reads EE_PROJECT, so the
   server-mode project field stays empty.

## Pre-review fix
Caddyfile now uses a file-based cert (active) with DNS-01 as a commented alternative; compose has commented cert mounts and a note that DNS_API_TOKEN is inert by default; README matches and notes EE_PROJECT has no effect in server mode. Concern 1 resolved. Still no docker build/Caddy validation (no daemon).

## Review fixes
Ignored deploy/*.key and deploy/*.crt; README documents uid 10001 / --user for the single-user path; DNS_API_TOKEN scrubbed from worker env with test coverage.
