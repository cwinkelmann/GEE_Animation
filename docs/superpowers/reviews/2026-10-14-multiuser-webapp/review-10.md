# Review Task 10 (static)
Spec: PASS. Quality: approved with Important follow-ups.
1. Important .gitignore:14 only ignores deploy/.env; deploy/carrot.key and carrot.crt (the files the README tells you to put in deploy/) are NOT ignored. Add deploy/*.key, deploy/*.crt.
2. Important README.md:217 single-user mount now runs as uid 10001; ~/.config/earthengine/credentials and key/ee-key.json are normally mode 600 owned by the host user, so unreadable on Linux. Note chmod/--user.
3. Minor compose.yaml:12 / .env.example:16 DNS_API_TOKEN reaches the app via env_file and is inherited by render workers (spawn.worker_env scrubs only GEE_OAUTH_*, GEE_COOKIE_SECRET). Inert unless set.
4. Minor compose.yaml:24-26 commented cert mounts: default `up` fails until uncommented; a missing ./carrot.crt makes Docker create a directory. README prerequisites cover it.
5. Minor .env.example: blank GEE_COOKIE_SECRET fails at startup (create_app ValueError, app.py:117), by design.
