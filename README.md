# OpenPiBot Agent Dashboard

A lightweight, single-file Python dashboard for monitoring and controlling multiple OpenPiBot-compatible AI agents. Runs on a Raspberry Pi (or any Linux host) behind Tailscale. No external dependencies — stdlib only.

## Purpose

Monitor multiple OpenPiBot agents from one browser. Chat with one agent or broadcast to many at once. Manage endpoints, review activity, and keep API keys server-side — never exposed to the browser.

## Live dashboard

**URL:** `https://mattpi.tail5f2bd.ts.net/` (Tailscale only)

## Security (production-hardened)

| Layer | Implementation |
|-------|---------------|
| HTTPS | Tailscale Serve with Let's Encrypt TLS |
| Auth | HTTP Basic Auth on all API endpoints + in-app login form |
| Firewall | iptables — only Tailscale subnet (`100.64.0.0/10`) can reach port 8766 |
| Rate limiting | 60 req/min on chat, 10 req/min on agent management |
| SSRF prevention | Agent URLs restricted to private IP ranges only |
| Security headers | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy |
| Secret redaction | API keys/tokens filtered from logs and responses |
| Session IDs | Randomized with `secrets.token_hex()` |
| File permissions | Config, logs, sessions all mode 600 |

See `docs/TAILSCALE_HARDENING.md` for the full hardening guide and `docs/SECURITY_NOTES.md` for the security posture.

## What it monitors

- Agent health (TCP probe + HTTP health check + model list)
- Per-agent latency, output tokens/sec, uptime history
- Real-time activity feed (chat requests, tool calls, completion metrics)
- Persistent JSONL logs (activity + chat)

## What you can do

- Chat with one agent or broadcast to multiple agents at once
- Manage agents (add/test/update/remove) — API keys stay on the Pi
- Cancel in-flight requests
- View session transcripts with context across agents
- Download raw logs for analysis

## Quick start on a Raspberry Pi

1. Copy the files:
   ```bash
   mkdir -p /home/ken/openpibot-dashboard
   cp src/dashboard.py /home/ken/openpibot-dashboard/dashboard.py
   cp config/endpoints.example.json /home/ken/openpibot-dashboard/endpoints.json
   chmod 600 /home/ken/openpibot-dashboard/endpoints.json
   ```

2. Create the auth env file:
   ```bash
   cp .env.example /home/ken/openpibot-dashboard/.env
   chmod 600 /home/ken/openpibot-dashboard/.env
   # Edit .env with your dashboard password
   ```

3. Edit `endpoints.json` with your agent URLs and API keys.

4. Install the systemd service:
   ```bash
   mkdir -p /home/ken/.config/systemd/user
   cp systemd/openpibot-dashboard.service /home/ken/.config/systemd/user/
   # Edit the service file — update EnvironmentFile path and env vars
   systemctl --user daemon-reload
   systemctl --user enable --now openpibot-dashboard.service
   loginctl enable-linger ken
   ```

5. (Recommended) Set up iptables and Tailscale Serve:
   ```bash
   sudo iptables -A INPUT -p tcp --dport 8766 -s 100.64.0.0/10 -j ACCEPT
   sudo iptables -A INPUT -p tcp --dport 8766 -j DROP
   sudo tailscale set --operator=$USER
   tailscale serve --bg http://PI_TAILSCALE_IP:8766
   ```

6. Open `https://pi-hostname.tailnet.ts.net/` and log in.

See `docs/INSTALL_AND_OPERATIONS.md` for the full guide.

## Package contents

| Path | Description |
|------|-------------|
| `src/dashboard.py` | Single-file Python dashboard (stdlib only) |
| `systemd/openpibot-dashboard.service` | User systemd service template |
| `config/endpoints.example.json` | Sanitized endpoint config example |
| `.env.example` | Environment variables template |
| `docs/FEATURE_LIST.md` | Feature catalog |
| `docs/PROOF_OF_CONCEPT_PROPOSAL.md` | POC proposal |
| `docs/INSTALL_AND_OPERATIONS.md` | Full install and operations guide |
| `docs/API_REFERENCE.md` | Dashboard API reference |
| `docs/TAILSCALE_HARDENING.md` | 9-layer security hardening guide |
| `docs/SECURITY_NOTES.md` | Security posture and incident response |

## Status

Production-hardened and running on a Raspberry Pi behind Tailscale. The hardening documented in this repo was applied June 14, 2026. Next steps: RBAC, command safety allowlists for plant/robotics use, formal agent registry.
