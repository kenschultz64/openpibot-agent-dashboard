# Tailscale Hardening Guide

This document describes the security hardening applied to the OpenPiBot dashboard for Tailscale exposure. Last updated: June 14, 2026.

## Current Hardening (all implemented)

### 1. HTTP Basic Authentication
All endpoints (HEAD, GET, POST) require authentication. Configured via environment variables:

```bash
OPENPIBOT_DASHBOARD_USER=admin
OPENPIBOT_DASHBOARD_PASS=<strong-password>
```

If no credentials are configured (`OPENPIBOT_DASHBOARD_USER` empty), auth is disabled (for development only).

Implementation details:
- Uses `secrets.compare_digest()` for timing-safe password comparison
- Returns 401 with `WWW-Authenticate: Basic` header
- Dashboard HTML page triggers browser's native auth dialog

### 2. Tailscale Serve for HTTPS
TLS termination via Tailscale Serve with automatic Let's Encrypt certificate:

```bash
sudo tailscale set --operator=$USER
tailscale serve --bg http://100.121.119.108:8766
```

This provides:
- Automatic HTTPS at `https://mattpi.tail5f2bd.ts.net/`
- TLS 1.3 with ChaCha20-Poly1305
- No manual certificate management
- Tailscale-managed cert rotation

### 3. iptables Firewall Rules
Port 8766 restricted to Tailscale subnet only (persisted to `/etc/iptables/rules.v4`):

```
ACCEPT tcp -- 100.64.0.0/10 → dpt:8766
DROP   tcp -- 0.0.0.0/0    → dpt:8766
```

### 4. Security Headers
All responses include:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Content-Security-Policy: default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'`
- `Access-Control-Allow-Origin` (matches request Origin)
- `Cache-Control: no-store`

### 5. Rate Limiting
In-memory per-IP rate limiting (resets on restart):
- Chat endpoints: 60 requests/minute
- Agent management endpoints: 10 requests/minute
- Rate limit window: 60 seconds
- Returns 429 with JSON error on excess

### 6. Agent URL IP Allowlisting (SSRF Prevention)
Agent management endpoints reject URLs pointing to non-private IP ranges. Allowed ranges:
- `100.64.0.0/10` — Tailscale
- `10.0.0.0/8` — Class A private
- `172.16.0.0/12` — Class B private
- `192.168.0.0/16` — Class C private
- `127.0.0.0/8` — loopback

Hostname resolution is rejected outright — only IP addresses in allowed ranges are accepted.

### 7. Server Header Suppression
`Server: OpenPiBot Dashboard` instead of `Server: BaseHTTP/0.6 Python/3.13.5`

### 8. Randomized Session IDs
Session IDs now include 4 bytes of random hex via `secrets.token_hex(4)`, making them unguessable.

### 9. CORS Preflight Support
OPTIONS handler returns 204 with security headers for cross-origin requests.

## Environment Variables

```bash
# Network
OPENPIBOT_DASHBOARD_HOST=0.0.0.0          # Dashboard bind address (firewall-gated)
OPENPIBOT_DASHBOARD_PORT=8766             # Dashboard port

# Authentication
OPENPIBOT_DASHBOARD_USER=admin            # Basic auth username (empty = disabled)
OPENPIBOT_DASHBOARD_PASS=<password>       # Basic auth password

# Rate Limiting
OPENPIBOT_DASHBOARD_RATE_CHAT=60          # Chat endpoint req/min
OPENPIBOT_DASHBOARD_RATE_AGENT=10         # Agent management req/min

# Polling
OPENPIBOT_DASHBOARD_POLL_INTERVAL=3       # Seconds between agent health polls
OPENPIBOT_DASHBOARD_TIMEOUT=5             # Seconds before agent HTTP timeout
```

## Systemd Service

```
[Unit]
Description=OpenPiBot endpoint real-time dashboard
After=network-online.target openpibot.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ken/openpibot-dashboard
EnvironmentFile=/home/ken/openpibot-dashboard/.env    # 0600 — contains auth creds
Environment=OPENPIBOT_DASHBOARD_DIR=/home/ken/openpibot-dashboard
Environment=OPENPIBOT_DASHBOARD_HOST=0.0.0.0
Environment=OPENPIBOT_DASHBOARD_PORT=8766
Environment=OPENPIBOT_DASHBOARD_POLL_INTERVAL=3
ExecStart=/usr/bin/python3 /home/ken/openpibot-dashboard/dashboard.py
Restart=unless-stopped
RestartSec=5

[Install]
WantedBy=default.target
```

The `.env` file at `/home/ken/openpibot-dashboard/.env` (mode 600):
```
OPENPIBOT_DASHBOARD_USER=admin
OPENPIBOT_DASHBOARD_PASS=<strong-password>
```

## Threat Model

| Exposure | Mitigations |
|----------|-------------|
| Tailscale peer accesses dashboard | Basic auth, HTTPS, security headers |
| Local network device scans port 8766 | iptables DROP for non-Tailscale sources |
| Malicious agent URL via agent management | IP allowlisting (private ranges only) |
| Brute-force auth attempts | Rate limiting (10 req/min on agent endpoints) |
| XSS via chat responses | Content-Security-Policy + output escaping |
| Clickjacking | X-Frame-Options: DENY |
| MIME sniffing | X-Content-Type-Options: nosniff |
| Session enumeration | Randomized session IDs with 32-bit entropy |

## Not Yet Implemented

- Tailscale ACLs restricting to specific users (requires Tailscale admin console)
- RBAC (viewer/operator/admin roles — Layer 4)
- Command safety allowlists (Layer 6 — needed for plant/robotics use)
- Audit logging of auth events (Layer 8)
- HTTPS without `-k` on non-Tailscale devices (Let's Encrypt cert is Tailscale-managed)

## Deployment Checklist

- [x] HTTP Basic Auth enabled (June 14 2026)
- [x] Tailscale Serve for HTTPS (June 14 2026)
- [x] Firewall restricting to Tailscale subnet (June 14 2026)
- [x] Security headers on all responses (June 14 2026)
- [x] Rate limiting on POST endpoints (June 14 2026)
- [x] Agent URL IP allowlisting (June 14 2026)
- [x] Randomized session IDs (June 14 2026)
- [x] Server header suppressed (June 14 2026)
- [ ] Tailscale ACLs restricting to specific users
- [ ] RBAC (viewer/operator/admin)
- [ ] Command safety allowlists for plant/robotics use
