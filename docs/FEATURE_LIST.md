# Feature List — OpenPiBot Multi-Agent Dashboard POC

## Executive summary

This proof of concept demonstrates a Raspberry Pi-based AI control dashboard that can monitor and control multiple OpenPiBot-compatible agents. It creates a practical path toward a low-cost plant-floor/device-management assistant that can move between PCs, Raspberry Pis, Pi Picos, sensors, robotic arms, and software tools from one browser interface.

## Core capabilities

### 1. Multi-agent monitoring

- Displays all configured agents/endpoints on one dashboard.
- Shows online/offline status.
- Shows health endpoint result.
- Shows model endpoint result.
- Shows response times.
- Shows the last time each endpoint was checked.
- Polls endpoints continuously in the background.
- Automatically reloads agent configuration when `endpoints.json` changes.

### 2. Chat / Control Console

- Send a command/prompt to one selected agent.
- Broadcast a command/prompt to all online agents.
- Uses OpenAI-compatible `/v1/chat/completions` API calls.
- Keeps API keys on the Pi server; keys are not exposed in browser JavaScript.
- Typed command clears from the input after send and appears in the conversation transcript.
- Enter sends; Shift+Enter inserts a newline.
- Supports a Stop button for active requests.
- Supports cancellation by request ID through `/api/chat/cancel`.

### 3. Persistent session context

- Maintains chat history per dashboard session.
- Maintains context per agent so separate bots do not get mixed together.
- Session ID can be viewed/changed.
- New Session button starts a fresh context.
- Clear Window clears the visible transcript without necessarily deleting stored context.
- Clear Context on Send lets the user send a one-off command without prior session history.
- Session files are stored privately on the Pi under `sessions/*.json`.

### 4. Manage Agents UI

- Browser-based Add/Test/Save/Remove agent form.
- No SSH or manual JSON editing required for normal users.
- Fields:
  - Agent ID
  - Display name
  - Kind
  - Location
  - Base URL
  - API key
  - Container/service note
- Test connection before saving.
- Save new agents.
- Update existing agents.
- Remove agents.
- API keys are write-only from the browser perspective:
  - The browser can see whether a key exists.
  - The browser cannot retrieve saved key values.
  - Leaving the API key field blank during edits keeps the existing stored key.

### 5. Command / Activity Monitor

- Displays recent tool and command activity reported by agents.
- Shows chat request activity.
- Stores redacted JSONL activity logs.
- Stores redacted chat logs.
- Rotates logs when they grow too large.
- Provides direct links to activity/chat logs for troubleshooting.

### 6. Reboot resilience

- Dashboard runs as a systemd user service.
- OpenPiBot runs as a systemd user service.
- User linger is enabled so services start at boot before login.
- Dashboard comes back automatically after Raspberry Pi reboot.

### 7. Device and software-control potential

The proof of concept supports the pattern needed to control:

- Raspberry Pi-connected hardware.
- Pi Pico / microcontroller programming workflows.
- Temperature sensors and other plant-floor sensors.
- Robotic arms and automation devices.
- PC applications through CLI or GUI automation layers.
- Drawing/design programs.
- Blender or CAD-like workflows for digital fabrication/3D printing.
- Multiple plant devices from one central console.

### 8. Security and safety behavior

- Agent API keys remain server-side.
- Sanitized `/api/agents` endpoint does not expose raw keys.
- Endpoint config file should remain `chmod 600`.
- Logs are redacted for common secret fields.
- Package excludes live credentials, logs, and session transcripts.
- Stop/cancel reduces risk from long-running commands.

## Current dashboard sections

1. Status summary cards
2. Agent status cards
3. Chat / Control Console
4. Manage Agents
5. Command / Activity Monitor

## Current dashboard API endpoints

- `GET /` — dashboard UI.
- `GET /api/status` — current endpoint health/status summary.
- `GET /api/agents` — sanitized configured-agent list.
- `POST /api/agents` — test/save/remove agents.
- `POST /api/chat` — send command to one/all agents.
- `POST /api/chat/cancel` — cancel an active dashboard chat request.
- `GET /api/session` — load transcript for session/agent.
- `GET /api/logs/summary` — log file metadata.
- `GET /api/logs/recent` — recent activity/chat log entries.
- `GET /logs/activity.jsonl` — activity log download/view.
- `GET /logs/chat.jsonl` — chat log download/view.

## Suggested next features

- Login/authentication for dashboard users.
- Role-based permissions for read-only monitoring vs device control.
- Installer script for fresh Raspberry Pis.
- Agent grouping by plant, room, production line, or device type.
- Visual alerting for offline/high-latency endpoints.
- Agent templates for common hardware/software configurations.
- Formal device registry and inventory fields.
- Command approval rules for dangerous operations.
- Built-in backup/restore from the dashboard.
- Optional HTTPS/Tailscale Serve exposure.
- Plant-management integrations such as maintenance logs, sensor history, and device checklists.
