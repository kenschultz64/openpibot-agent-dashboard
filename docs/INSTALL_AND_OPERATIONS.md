# Install and Operations Guide

## Runtime model

The dashboard is a single Python file served by the Raspberry Pi. It reads agent endpoint definitions from `endpoints.json`, polls those agents, and exposes a web UI plus JSON APIs.

## Directory layout

Recommended Pi layout:

```text
/home/ken/openpibot-dashboard/
  dashboard.py
  endpoints.json
  sessions/
  logs/
```

Recommended user systemd service:

```text
/home/ken/.config/systemd/user/openpibot-dashboard.service
```

## Configuration file

Main config:

```text
/home/ken/openpibot-dashboard/endpoints.json
```

This file contains agent URLs and API keys. Keep it private:

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

## Starting manually

```bash
cd /home/ken/openpibot-dashboard
OPENPIBOT_DASHBOARD_HOST=0.0.0.0 \
OPENPIBOT_DASHBOARD_PORT=8766 \
python3 dashboard.py
```

## Installing the systemd user service

```bash
mkdir -p /home/ken/.config/systemd/user
cp systemd/openpibot-dashboard.service /home/ken/.config/systemd/user/openpibot-dashboard.service
systemctl --user daemon-reload
systemctl --user enable --now openpibot-dashboard.service
```

Enable boot startup before login:

```bash
sudo loginctl enable-linger ken
```

If running as user `ken` and polkit permits it, this may also work without a password prompt:

```bash
loginctl enable-linger ken
```

## Service commands

```bash
systemctl --user status openpibot-dashboard.service --no-pager
systemctl --user restart openpibot-dashboard.service
systemctl --user stop openpibot-dashboard.service
systemctl --user start openpibot-dashboard.service
journalctl --user -u openpibot-dashboard.service -n 100 --no-pager
```

## Environment variables

The service supports these environment variables:

- `OPENPIBOT_DASHBOARD_DIR` — app data directory.
- `OPENPIBOT_DASHBOARD_HOST` — bind host, normally `0.0.0.0` for LAN/Tailscale access.
- `OPENPIBOT_DASHBOARD_PORT` — dashboard port, currently `8766`.
- `OPENPIBOT_DASHBOARD_POLL_INTERVAL` — endpoint polling interval in seconds.
- `OPENPIBOT_DASHBOARD_SESSION_DIR` — optional override for session storage.

## Verifying the dashboard

```bash
curl -i http://PI_IP:8766/
curl -s http://PI_IP:8766/api/status
curl -s http://PI_IP:8766/api/agents
```

Expected:

- `/` returns HTTP 200.
- `/api/status` returns endpoint status JSON.
- `/api/agents` returns sanitized agent metadata and does not include raw API keys.

## Reboot behavior

To ensure reboot startup:

```bash
loginctl show-user ken -p Linger --value
systemctl --user is-enabled openpibot-dashboard.service
systemctl --user is-enabled openpibot.service
```

Expected:

```text
yes
enabled
enabled
```

After a reboot, open:

```text
http://PI_IP:8766
```

## Adding agents

Preferred method:

1. Open the dashboard.
2. Scroll to **Manage Agents**.
3. Fill in the fields.
4. Click **Test**.
5. If online, click **Save**.

Manual fallback:

1. Edit `/home/ken/openpibot-dashboard/endpoints.json`.
2. Keep permissions at `600`.
3. The dashboard polling loop should reload config changes automatically.
4. Restart the service if needed.

## Logs and sessions

- Activity log: `/home/ken/openpibot-dashboard/logs/activity.jsonl`
- Chat log: `/home/ken/openpibot-dashboard/logs/chat.jsonl`
- Sessions: `/home/ken/openpibot-dashboard/sessions/*.json`

These files may include prompts, responses, and operational metadata. Treat them as private.

## Packaging rule

Do not package live `endpoints.json`, live sessions, or live logs unless they have been reviewed and redacted. Use `config/endpoints.example.json` instead.
