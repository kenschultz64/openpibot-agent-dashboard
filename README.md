# OpenPiBot Multi-Agent Dashboard Proof of Concept

This package documents and packages the Raspberry Pi OpenPiBot dashboard proof of concept.

## Purpose

The proof of concept shows that a Raspberry Pi can act as a low-cost, network-connected AI control node for devices, programs, and plant-floor equipment. A web dashboard monitors multiple OpenPiBot-compatible agents and gives a user a single browser-based command/control console.

The broader idea is a lightweight, Hermes-like agent control layer that can move quickly from device to device, computer to computer, or plant cell to plant cell.

## What was proven

- OpenPiBot can run on a Raspberry Pi and expose an OpenAI-compatible API.
- A browser dashboard can monitor several agents at once.
- A user can chat with/control one agent or broadcast to multiple agents.
- The agents can run tools, execute commands, and control local software or connected devices.
- API keys can stay server-side instead of being exposed to the browser.
- User-level systemd services can bring the dashboard back after reboot.
- A Pi Pico or similar microcontroller can be programmed/controlled from the agent layer.
- This approach can scale toward robotic arms, sensors, smart devices, plant-management utilities, and PC application control.

## Live proof-of-concept dashboard

Current internal/Tailscale URL:

`http://100.121.119.108:8766`

Current Pi host:

- Hostname: `mattpi`
- Dashboard directory: `/home/ken/openpibot-dashboard`
- Dashboard service: `openpibot-dashboard.service`
- Native Pi OpenPiBot service: `openpibot.service`

## Package contents

- `src/dashboard.py` — single-file Python dashboard app.
- `systemd/openpibot-dashboard.service` — user systemd service example.
- `config/endpoints.example.json` — sanitized endpoint configuration example.
- `docs/FEATURE_LIST.md` — detailed feature list for email/proposal use.
- `docs/PROOF_OF_CONCEPT_PROPOSAL.md` — proposal-style writeup.
- `docs/INSTALL_AND_OPERATIONS.md` — setup, reboot, and operations guide.
- `docs/SECURITY_NOTES.md` — credentials and safety notes.
- `docs/API_REFERENCE.md` — dashboard API reference.

## Important security note

This package intentionally does **not** include the live `endpoints.json`, live chat sessions, or logs because those may contain API keys, prompts, internal URLs, or other sensitive information. Use `config/endpoints.example.json` as the safe starting point.

## Quick start on a Raspberry Pi

1. Copy the package contents to the Pi, for example:

   ```bash
   mkdir -p /home/ken/openpibot-dashboard
   cp src/dashboard.py /home/ken/openpibot-dashboard/dashboard.py
   cp config/endpoints.example.json /home/ken/openpibot-dashboard/endpoints.json
   chmod 600 /home/ken/openpibot-dashboard/endpoints.json
   ```

2. Edit `/home/ken/openpibot-dashboard/endpoints.json` and add real agent URLs/API keys.

3. Install the systemd user service:

   ```bash
   mkdir -p /home/ken/.config/systemd/user
   cp systemd/openpibot-dashboard.service /home/ken/.config/systemd/user/openpibot-dashboard.service
   systemctl --user daemon-reload
   systemctl --user enable --now openpibot-dashboard.service
   loginctl enable-linger ken
   ```

4. Open the dashboard:

   ```text
   http://PI_TAILSCALE_OR_LAN_IP:8766
   ```

## Proof-of-concept status

This is a working internal proof of concept. The next step would be hardening, authentication, installer scripts, a more formal agent registry, and a production deployment model for plant or lab use.
