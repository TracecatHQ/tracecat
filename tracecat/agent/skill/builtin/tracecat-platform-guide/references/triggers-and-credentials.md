# Triggers, credentials, and custom actions

## Picking a trigger

All trigger types except where noted are configured **inside the workflow builder**
(`/workflows/<id>`), not on a separate page.

| Trigger | Fires when | Use for |
|---|---|---|
| **Schedule** | A cron/interval cadence | Polling, syncs, cleanup, recurring jobs |
| **Webhook** | An external system POSTs/GETs to the workflow URL | Ingesting alerts/events; body becomes `${{ TRIGGER }}` |
| **Case trigger** | A case event (create, update, close, comment, tag, task) | Reacting to case activity; supports a tag allowlist |
| **Comment trigger** | A case comment/reply is posted (configured in the comment UI) | Bridging analyst chat into automation |
| **Task trigger** | A case task is launched (configured on the task) | Running a workflow from a case task |
| **Error workflow** | Another (published) workflow fails | Centralized failure handling; set in workflow settings |

Webhooks can be secured with API keys, an IP allowlist, and allowed methods.

## The three credential models

Pick based on how the target system authenticates.

**Prebuilt credentials (workspace secrets)** — static API keys, bot tokens, SSH keys,
mTLS certs. Define them under **Credentials** (`/credentials`) per environment. Reference
as `${{ SECRETS.<name>.<KEY> }}`. This is the default for most integrations.

**OAuth integrations** — managed OAuth tokens, auto-refreshed. Two grant types:
`authorization_code` (a user logs in / delegated access) and `client_credentials`
(service-to-service). Configure under **Integrations** (`/integrations`), then complete
the OAuth connect flow. Reference as
`${{ SECRETS.<provider_id>_oauth.<...TOKEN> }}`. Built-in providers have stable IDs;
custom ones use a `custom_` prefix.

**MCP integrations** — Model Context Protocol servers, remote (HTTP/SSE) or stdio.
Configure remote servers under **MCP servers** (`/mcp-servers`); agents attach them from
the preset builder. Remote supports no-auth, custom headers, or OAuth. Stdio env vars can
resolve `${{ SECRETS.* }}` and `${{ VARS.* }}`.

## Custom extensibility

- **Python UDFs** — register async functions in a custom registry package to add new
  `tools.*` actions.
- **YAML templates** — compose existing actions into a reusable action (small, no control
  flow).
- **Custom registry** — a git-backed repo of custom Python/YAML actions, synced org-wide;
  configured in organization settings.

## Authentication options (product-level)

- **Basic** — email + password (dev/simple setups).
- **OIDC** — OpenID Connect SSO.
- **SAML 2.0** — enterprise SSO (Okta, Entra ID, Authentik, Keycloak).
