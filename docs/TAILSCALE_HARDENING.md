# Tailscale Hardening Guide

This document covers how to secure the OpenPiBot dashboard for Tailscale exposure.

## Threat model

- **Closed LAN (current):** Dashboard is behind a firewall, only accessible from trusted LAN/Tailscale peers. No authentication required.
- **Tailscale exposure (target):** Dashboard is reachable from other Tailscale peers. Needs authentication, HTTPS, and access control.
- **Internet exposure (not recommended):** Requires full auth, HTTPS, rate limiting, and WAF.

## Recommended Tailscale hardening (in order of priority)

### 1. Add HTTP basic authentication

Simplest first layer of defense. Add to `dashboard.py` before any route handling:

```python
import base64

DASHBOARD_AUTH_USER = os.environ.get("OPENPIBOT_DASHBOARD_AUTH_USER", "admin")
DASHBOARD_AUTH_PASS = os.environ.get("OPENPIBOT_DASHBOARD_AUTH_PASS", "")

def check_auth(self) -> bool:
    auth = self.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="OpenPiBot Dashboard"')
        self.end_headers()
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode()
        user, password = decoded.split(":", 1)
        if user == DASHBOARD_AUTH_USER and password == DASHBOARD_AUTH_PASS:
            return True
    except Exception:
        pass
    self.send_response(401)
    self.send_header("WWW-Authenticate", 'Basic realm="OpenPiBot Dashboard"')
    self.end_headers()
    return False
```

Call `check_auth()` at the top of `do_GET` and `do_POST`.

### 2. Use Tailscale Serve for HTTPS

Instead of self-managing TLS certificates, use Tailscale Serve:

```bash
# On the Pi (or a Tailscale node that can reach the Pi)
tailscale serve --bg https://openpibot-dashboard 100.121.119.108:8766
```

This gives you:
- Automatic HTTPS via Tailscale's cert authority
- DNS name: `https://openpibot-dashboard` (or whatever name you choose)
- No certificate management

### 3. Restrict with Tailscale ACLs

In your Tailscale network ACL (`tailscale/acl.json`), restrict dashboard access:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["user:ken@example.com"],
      "dst": ["tag:openpibot-dashboard:8766"]
    }
  ]
}
```

### 4. Bind to Tailscale IP only

For extra safety, bind only to the Tailscale IP instead of `0.0.0.0`:

```bash
OPENPIBOT_DASHBOARD_HOST=100.121.119.108
```

This ensures the dashboard is only reachable via Tailscale.

### 5. Add API key authentication for agent management

The `/api/agents` POST endpoint should require an additional API key for write operations:

```python
DASHBOARD_API_KEY = os.environ.get("OPENPIBOT_DASHBOARD_API_KEY", "")

def check_api_key(self) -> bool:
    key = self.headers.get("X-Dashboard-API-Key", "")
    if key == DASHBOARD_API_KEY:
        return True
    self.send_error(403, "Forbidden: invalid API key")
    return False
```

### 6. Rate limiting

Add simple rate limiting to prevent abuse:

```python
import time
from collections import defaultdict

RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 100    # requests per window

rate_limits = defaultdict(list)

def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    rate_limits[client_ip] = [t for t in rate_limits[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(rate_limits[client_ip]) >= RATE_LIMIT_MAX:
        return False
    rate_limits[client_ip].append(now)
    return True
```

### 7. Input validation

- Validate agent IDs against a safe character set (alphanumeric, dash, underscore, dot)
- Validate URLs to prevent SSRF
- Sanitize all user input before writing to `endpoints.json`

### 8. Audit logging

Log all dashboard actions (agent add/remove/test, chat sends, cancellations) with timestamps and source IPs.

### 9. CORS protection

For Tailscale exposure, restrict CORS to only allow requests from the dashboard itself:

```python
self.send_header("Access-Control-Allow-Origin", "https://openpibot-dashboard")
```

## Deployment checklist for Tailscale

- [ ] HTTP basic auth enabled
- [ ] Tailscale Serve configured for HTTPS
- [ ] Tailscale ACLs restrict access to authorized users
- [ ] Dashboard binds to Tailscale IP only
- [ ] API key required for agent management endpoints
- [ ] Rate limiting enabled
- [ ] Input validation on all endpoints
- [ ] Audit logging enabled
- [ ] CORS restricted to dashboard origin
- [ ] No sensitive data in logs

## Environment variables for production

```bash
# Required for Tailscale deployment
OPENPIBOT_DASHBOARD_HOST=100.121.119.108
OPENPIBOT_DASHBOARD_PORT=8766
OPENPIBOT_DASHBOARD_AUTH_USER=admin
OPENPIBOT_DASHBOARD_AUTH_PASS=STRONG_PASSWORD_HERE
OPENPIBOT_DASHBOARD_API_KEY=STRONG_API_KEY_HERE
```

## Alternative: Tailscale Funnel (internet exposure)

**Not recommended** for this dashboard without significant hardening. If you must expose to the internet:

1. Use Tailscale Funnel instead of Serve
2. Add strong authentication (not just basic auth)
3. Add rate limiting
4. Add CSRF protection
5. Use a WAF (Web Application Firewall)
6. Monitor logs for abuse

```bash
tailscale funnel --bg https://openpibot-dashboard-public 100.121.119.108:8766
```

## Notes

- The dashboard currently uses Python's built-in `http.server` which is not designed for production use.
- For production, consider migrating to a proper web framework (Flask, FastAPI, etc.)
- The current implementation has no session management or persistent authentication.
- All security measures should be tested before deployment.
