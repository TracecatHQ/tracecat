## Local Dex proof assets

This directory holds only the local proof inputs for the Dex-backed MCP refresh test.

Normal local Docker Compose uses `deployments/dex/config.docker.yaml`. In standalone
mode, that config now enables Dex's local password DB automatically when no upstream
OIDC issuer/client settings are present. Tracecat can then provision Dex users over
Dex gRPC; a static local user is optional rather than required.

Files:
- `local-proof.config.yaml.tmpl`: Dex config template for a short-lived proof setup.
- `local-proof.env.example`: Example environment values for rendering the template.

Local proof intent:
- issue Dex access/ID tokens with a very short lifetime, such as `5s`
- keep refresh tokens valid long enough to test repeated refreshes
- point Dex upstream at PropelAuth through Dex's OIDC connector
- send the upstream callback back through the main Tracecat host rather than the Dex host
- register Tracecat's app and MCP callbacks as Dex static client redirects

Render the config with:

```bash
python scripts/render_dex_local_proof_config.py \
  --env-file deployments/dex/local-proof.env \
  --output /tmp/dex.local-proof.yaml
```

Then point your Dex container at the rendered file for the short-expiry proof.
