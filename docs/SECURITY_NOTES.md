# Security Notes — OpenPiBot Dashboard

Last updated: June 14, 2026

## Current Security Posture

### Access
- **URL:** `https://mattpi.tail5f2bd.ts.net/` (Tailscale only)
- **Auth:** HTTP Basic Authentication
- **TLS:** Tailscale Serve with Let's Encrypt (auto-renewed)

### Attack Surface
- Only reachable via Tailscale network (iptables DROP for all other sources)
- Agent management restricted to private IP ranges only
- Rate limited (60/min chat, 10/min agent management)

### Credential Storage
- API keys stored in `endpoints.json` (mode 0600)
- Auth password stored in systemd environment variables
- No secrets committed to git repository (`.gitignore` excludes `endpoints.json`, `.env`)
- Session files at mode 0600

### Secret Redaction
- All API keys, tokens, and passwords filtered from logs before write
- Bearer tokens in agent responses redacted before display
- Dashboard `/api/agents` endpoint shows `api_key_set: true/false` but never the key value
- Sanitization regex patterns:
  - `Bearer <token>` → `Bearer [redacted]`
  - `api_key=...` / `token=...` / `password=...` → `[key]=[redacted]`

### Logs
- Activity log: `logs/activity.jsonl` — redacted, rotated at 10MB
- Chat log: `logs/chat.jsonl` — redacted, rotated at 10MB
- Session files: `sessions/*.json` — redacted, mode 0600
- Logs accessible via `/logs/` endpoint (requires auth)

### Dependencies
- **Zero external dependencies** — stdlib only (Python 3.11+)
- No npm packages, no pip packages beyond stdlib
- No database — JSON files on disk

## Production Deployment Notes

### Pi (mattpi)
- Host: Raspberry Pi (Debian aarch64)
- IP: `100.121.119.108` (Tailscale), `192.168.1.27` (WiFi)
- Service: `openpibot-dashboard.service` (user systemd)
- Firewall: iptables (persisted to `/etc/iptables/rules.v4`)
- Backup: `dashboard.py.bak-YYYYMMDD_HHMMSS`

### Agent Endpoints
- 10 agents monitored (grantbot, cwserver-ggnworker, pi-openpibot, Pi-Agent, Docbot, megbot, codybot, nigelbot, abdobot, kathybot)
- All on Tailscale IPs in `100.64.0.0/10`
- API keys stored in `endpoints.json` (mode 0600)

## Incident Response

If the dashboard is compromised:
1. Rotate all agent API keys
2. Change dashboard auth password
3. Check `logs/chat.jsonl` for unauthorized commands
4. Review iptables rules (`sudo iptables -L -n`)
5. Check for modified `endpoints.json`

## Future Improvements

- [ ] Move from Basic Auth to session-based auth with tokens
- [ ] Add RBAC (viewer/operator/admin)
- [ ] Encrypt `endpoints.json` at rest
- [ ] Add fail2ban for repeated auth failures
- [ ] Add audit logging of auth events (login success/failure)
- [ ] Command safety allowlists for production plant/robotics use
- [ ] Tailscale ACLs restricting to specific users
- [ ] Consider migrating from `http.server` to FastAPI/Flask
