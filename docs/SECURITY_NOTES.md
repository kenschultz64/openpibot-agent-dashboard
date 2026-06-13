# Security Notes

## Credential handling

The live dashboard uses `endpoints.json` to store agent API keys. This file should not be published or sent externally.

Required permission:

```bash
chmod 600 /home/ken/openpibot-dashboard/endpoints.json
```

The browser-facing API intentionally returns only sanitized agent metadata. It should not reveal raw API keys.

## What this package excludes

This proof-of-concept package excludes:

- Live `endpoints.json` with real API keys.
- Backup copies of live endpoint files.
- Live chat sessions.
- Live activity logs.
- Live chat logs.
- Any key files or bridge secrets.

Use `config/endpoints.example.json` as the safe starting point.

## Logs

The app redacts common secret fields such as:

- `api_key`
- `authorization`
- `token`
- `password`
- `secret`

Even with redaction, logs can still contain sensitive operational details, user prompts, filenames, hostnames, internal IPs, or command outputs. Treat logs as private.

## Dashboard exposure

For a proof of concept, Tailscale-only access is preferred. Avoid exposing the dashboard directly to the public internet without adding authentication and HTTPS.

Recommended next hardening steps:

1. Add login/authentication.
2. Add role-based permissions.
3. Require confirmation for dangerous commands.
4. Use HTTPS or Tailscale Serve/Funnel appropriately.
5. Add audit log review/export tools.
6. Separate read-only monitoring from command/control operations.
7. Add endpoint groups and permission scopes.

## Command safety

This dashboard can send commands to agents that may have shell or device-control capabilities. Treat it like a control plane, not just a status page.

For plant or robotics use, add safeguards before production:

- Allowlist safe commands where possible.
- Require operator approval for movement/destructive actions.
- Use emergency stop outside the AI layer.
- Keep human-observable indicators for active operations.
- Log all commands and results.
- Keep device-level safety interlocks in place.

## API key rotation

If a key is accidentally exposed:

1. Revoke/rotate the exposed key.
2. Update `endpoints.json` through the Manage Agents UI or by editing the file directly.
3. Confirm file permissions remain `600`.
4. Restart the affected agent service if required.
