# API Reference

All API endpoints except `/` require HTTP Basic Authentication. Requests without valid credentials receive HTTP 401.

**Required header:** `Authorization: Basic <base64(user:pass)>`

Rate limits:
- Chat endpoints: 60 requests/minute per IP
- Agent management endpoints: 10 requests/minute per IP

## External Agent Monitoring & Control

The dashboard API is designed to be used by other AI agents (like Hermes) for automated monitoring and orchestration. No separate agent API key is needed — use the same Basic Auth credentials.

### Monitor all agents from an agent

```bash
curl -s -u admin:password http://pi:8766/api/status | jq '.summary'
# {"total": 10, "online": 10, "degraded": 0, "offline": 0}
```

### Send a prompt to a specific agent

```bash
curl -s -u admin:password -X POST http://pi:8766/api/chat \
  -H "Content-Type: application/json" \
  -d '{"target":"grantbot","message":"Check your health and report status","session_id":"monitor-001"}'
```

### Broadcast to all agents

```bash
curl -s -u admin:password -X POST http://pi:8766/api/chat \
  -H "Content-Type: application/json" \
  -d '{"target":"all","message":"Report your current model and uptime","session_id":"monitor-001"}'
```

### Cancel an active request

```bash
curl -s -u admin:password -X POST http://pi:8766/api/chat/cancel \
  -H "Content-Type: application/json" \
  -d '{"request_id":"req-1718400000000-a1b2c3"}'
```

### Automated health check (cron-friendly)

```bash
STATUS=$(curl -s -u admin:password http://pi:8766/api/status)
OFFLINE=$(echo "$STATUS" | jq '.summary.offline')
if [ "$OFFLINE" -gt 0 ]; then
  echo "ALERT: $OFFLINE agents offline"
  echo "$STATUS" | jq '.endpoints[] | select(.status=="offline") | {name, base_url}'
fi
```

## `GET /`

Returns the dashboard HTML interface.

## `GET /api/status`

Returns current polling status for configured endpoints.

Typical uses:

- Dashboard health cards.
- Online/offline checks.
- Agent model information.
- Recent activity summary.

## `GET /api/agents`

Returns sanitized configured-agent metadata.

Important behavior:

- Does not return raw API keys.
- Returns `api_key_set: true` or `false`.
- Safe for the browser UI.

## `POST /api/agents`

Manages dashboard endpoints. Agent URLs are restricted to private IP ranges only (Tailscale `100.64.0.0/10`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`). Hostnames are rejected.

### Test agent

Request:

```json
{
  "action": "test",
  "id": "pi-openpibot",
  "name": "Pi OpenPiBot",
  "base_url": "http://100.121.119.108:11437",
  "api_key": "REPLACE_WITH_AGENT_API_KEY"
}
```

Response includes health/model probe results.

### Save agent

Request:

```json
{
  "action": "save",
  "id": "pi-openpibot",
  "name": "Pi OpenPiBot",
  "kind": "Native service",
  "location": "Raspberry Pi",
  "base_url": "http://100.121.119.108:11437",
  "api_key": "REPLACE_WITH_AGENT_API_KEY",
  "container": "openpibot.service"
}
```

If editing an existing agent and `api_key` is blank, the existing stored key is retained.

### Remove agent

Request:

```json
{
  "action": "remove",
  "id": "pi-openpibot"
}
```

Removes the endpoint from `endpoints.json`.

## `POST /api/chat`

Sends a prompt to one agent or all online agents.

Request:

```json
{
  "target": "pi-openpibot",
  "message": "Check your health and report status.",
  "session_id": "session-123",
  "clear_context": false,
  "request_id": "req-123"
}
```

Target may be:

- A configured agent ID.
- `all` to broadcast to all online agents.

## `POST /api/chat/cancel`

Cancels an active dashboard chat request.

Request:

```json
{
  "request_id": "req-123"
}
```

The dashboard closes the active connection to the backend agent. Bridge agents should handle client disconnects and abort their local session where possible.

## `GET /api/session`

Loads a session transcript.

Query parameters:

- `session_id`
- `target`

Example:

```text
/api/session?session_id=session-123&target=pi-openpibot
```

## `GET /api/logs/summary`

Returns log file metadata such as path, existence, and size.

## `GET /api/logs/recent`

Returns recent JSONL log entries.

Query parameters:

- `kind=activity` or `kind=chat`
- `limit=50`

## `GET /logs/activity.jsonl`

Returns the activity log file.

## `GET /logs/chat.jsonl`

Returns the chat log file.

## Agent compatibility expectations

Each controlled agent should ideally expose:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- Optional: `GET /activity`

The `/v1/chat/completions` route should be OpenAI-compatible enough for a message payload containing `model` and `messages`.
