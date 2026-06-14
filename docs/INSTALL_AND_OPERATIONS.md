# Install and Operations Guide

## Runtime model

The dashboard is a single Python file served by the Raspberry Pi. It reads agent endpoint definitions from `endpoints.json`, polls those agents, and exposes a web UI plus JSON APIs. Authentication is handled via `.env` file credentials with Basic Auth on API endpoints and an in-app login form.

## Directory layout

Recommended Pi layout:

```text
/home/ken/openpibot-dashboard/
  dashboard.py
  endpoints.json        (mode 600)
  .env                  (mode 600 — auth credentials)
  sessions/
  logs/
```

Recommended user systemd service:

```text
/home/ken/.config/systemd/user/openpibot-dashboard.service
```

## Configuration files

### endpoints.json

Main config — contains agent URLs and API keys. Keep it private:

```bash
chmod 600 /home/ken/openpibot-dashboard/endpoints.json
```

Example structure:

```json
[
  {
    "id": "pi-openpibot",
    "name": "Pi OpenPiBot",
    "kind": "Native service",
    "location": "Raspberry Pi / Tailscale",
    "base_url": "http://100.121.119.108:11437",
    "api_key": "REPLACE_WITH_AGENT_API_KEY",
    "container": "openpibot.service"
  }
]
```

### .env (auth credentials)

```bash
cp .env.example /home/ken/openpibot-dashboard/.env
chmod 600 /home/ken/openpibot-dashboard/.env
# Edit with your dashboard username and password
```

## Starting manually

```bash
cd /home/ken/openpibot-dashboard
source .env
python3 dashboard.py
```

## Installing the systemd user service

```bash
mkdir -p /home/ken/.config/systemd/user
cp systemd/openpibot-dashboard.service /home/ken/.config/systemd/user/openpibot-dashboard.service
# Edit the service file: update EnvironmentFile path
systemctl --user daemon-reload
systemctl --user enable --now openpibot-dashboard.service
```

Enable boot startup before login:

```bash
sudo loginctl enable-linger ken
```

## Service commands

```bash
systemctl --user status openpibot-dashboard.service --no-pager
systemctl --user restart openpibot-dashboard.service
systemctl --user stop openpibot-dashboard.service
systemctl --user start openpibot-dashboard.service
journalctl --user -u openpibot-dashboard.service -n 100 --no-pager
```

## Firewall (iptables)

Restrict port 8766 to Tailscale subnet only:

```bash
sudo iptables -A INPUT -p tcp --dport 8766 -s 100.64.0.0/10 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8766 -j DROP
# Persist rules
sudo mkdir -p /etc/iptables
sudo iptables-save | sudo tee /etc/iptables/rules.v4
```

## Tailscale Serve (HTTPS)

```bash
sudo tailscale set --operator=$USER
tailscale serve --bg http://PI_TAILSCALE_IP:8766
```

This provides automatic HTTPS at `https://pi-hostname.tailnet.ts.net/` with Let's Encrypt TLS.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENPIBOT_DASHBOARD_DIR` | `/home/ken/openpibot-dashboard` | App data directory |
| `OPENPIBOT_DASHBOARD_HOST` | `0.0.0.0` | Bind address |
| `OPENPIBOT_DASHBOARD_PORT` | `8766` | Dashboard port |
| `OPENPIBOT_DASHBOARD_POLL_INTERVAL` | `3` | Endpoint poll interval (seconds) |
| `OPENPIBOT_DASHBOARD_TIMEOUT` | `5` | Agent HTTP timeout (seconds) |
| `OPENPIBOT_DASHBOARD_USER` | — | Basic Auth username (empty = auth disabled) |
| `OPENPIBOT_DASHBOARD_PASS` | — | Basic Auth password |
| `OPENPIBOT_DASHBOARD_RATE_CHAT` | `60` | Chat req/min per IP |
| `OPENPIBOT_DASHBOARD_RATE_AGENT` | `10` | Agent management req/min per IP |
| `OPENPIBOT_DASHBOARD_CONFIG` | `endpoints.json` | Config override |
| `OPENPIBOT_DASHBOARD_SESSION_DIR` | `sessions/` | Session storage override |
| `OPENPIBOT_DASHBOARD_LOG_DIR` | `logs/` | Log directory override |
| `OPENPIBOT_DASHBOARD_LOG_MAX_BYTES` | `10485760` | Log rotation threshold |

## Verifying the dashboard

```bash
# HTML page loads without auth
curl -sI http://PI_IP:8766/

# API endpoints require auth
curl -sI http://PI_IP:8766/api/status
# → HTTP 401

# With credentials
curl -s -u admin:password http://PI_IP:8766/api/status
# → endpoint status JSON

curl -s -u admin:password http://PI_IP:8766/api/agents
# → sanitized agent metadata (no raw API keys)

# HTTPS (after Tailscale Serve setup)
curl -sk --resolve pi-hostname.tailnet.ts.net:443:PI_TAILSCALE_IP \
  -u admin:password https://pi-hostname.tailnet.ts.net/api/status
```

## Reboot behavior

To ensure reboot startup:

```bash
loginctl show-user ken -p Linger --value  # → yes
systemctl --user is-enabled openpibot-dashboard.service  # → enabled
systemctl --user is-enabled openpibot.service  # → enabled
```

After a reboot, open:

```text
https://pi-hostname.tailnet.ts.net/
```

## Adding agents

Preferred method:

1. Open the dashboard and log in.
2. Scroll to **Manage Agents**.
3. Fill in the fields.
4. Click **Test**.
5. If online, click **Save**.

Manual fallback:

1. Edit `/home/ken/openpibot-dashboard/endpoints.json`.
2. Keep permissions at `600`.
3. The dashboard polling loop reloads config changes automatically.
4. Restart the service if needed.

## Logs and sessions

- Activity log: `/home/ken/openpibot-dashboard/logs/activity.jsonl`
- Chat log: `/home/ken/openpibot-dashboard/logs/chat.jsonl`
- Sessions: `/home/ken/openpibot-dashboard/sessions/*.json`

These files may include prompts, responses, and operational metadata. Treat them as private. All logs are redacted — API keys, tokens, and passwords are filtered before write.

## Packaging rule

Do not package live `endpoints.json`, live sessions, or live logs unless they have been reviewed and redacted. Use `config/endpoints.example.json` instead.
