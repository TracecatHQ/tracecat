# Preview Devices

`tracecat-preview-devices` is the local AI SPM QA harness for the existing
Tracecat dev stack. It runs three long-lived synthetic Claude Code endpoints in
containers, points them at the normal local app origin, and makes their synced
inventory visible in the real `/spm` UI.

## Topology

- Start the normal local stack with `just cluster up -d`.
- Cross-build `packages/tracecat-endpoint/dist/tracecatd` for Linux on the host.
- Materialize immutable tracked templates from `e2e/scenarios/` into
  `dist/preview-devices/<scenario>/home/`.
- Persist device state in `dist/preview-devices/<scenario>/state/state.json`.
- Run the independent compose project `tracecat-preview-devices`.

The preview stack is intentionally separate from the main Tracecat compose
project. The containers talk to the public app origin exactly like external
endpoints do.

## Commands

```bash
just preview-devices up -d
just preview-devices ps
just preview-devices logs
just preview-devices down
```

`up` performs the full QA bootstrap:

- verifies `docker`, `curl`, `jq`, and `go`
- resolves the running Tracecat cluster and public app origin
- logs in with the seeded tenant dev user
- defaults to `TRACECAT__DEV_USER_EMAIL=dev@tracecat.com`
- defaults to `TRACECAT__DEV_USER_PASSWORD=password1234`
- resolves the tenant org context from `GET /api/organization`
- creates or reuses preview endpoint enrollments
- cross-builds a Linux `dist/tracecatd`
- copies tracked fixtures into `dist/preview-devices/`
- starts the `tracecat-preview-devices` compose stack
- waits for all preview endpoints to appear at `/spm/endpoints`

## Scenarios

- `baseline`: clean inventory and endpoint visibility for a trusted Claude
  workspace with benign instruction files.
- `rogue_mcp`: project `.mcp.json` inventory plus a manual QA path for
  `disabledMcpjsonServers` enforcement without mutating `.mcp.json`.
- `rogue_instruction_file`: deterministic local enforcement path for risky
  instruction-file findings via `claudeMdExcludes`.

## Runtime Layout

- `e2e/scenarios/<scenario>/home/`: immutable tracked template source
- `dist/preview-devices/<scenario>/home/`: mutable runtime Claude home copy
- `dist/preview-devices/<scenario>/state/state.json`: persisted endpoint state
- `dist/preview-devices/<scenario>/device.env`: endpoint enrollment/env file
- `dist/preview-devices/session/cookies.txt`: authenticated bootstrap session

The wrapper replaces `__HOME__` with `/home/tracecat` when materializing the
runtime home copies and rewrites `workspace-alpha` into a scenario-specific
runtime directory such as `workspace-baseline` or `workspace-rogue-mcp`. It
deletes and recreates only the runtime `home/` tree on each `up`, so
enforcement writes never touch tracked fixtures while persisted endpoint state
survives normal restarts.

## Manual QA

After `just preview-devices up -d`, open the SPM UI and verify:

1. `Preview Baseline`, `Preview Rogue MCP`, and `Preview Rogue Instruction File`
   appear under `/spm/endpoints`.
2. Each endpoint detail view shows assets from the synced Claude home.
3. `Preview Rogue Instruction File` produces a finding for the risky
   `CLAUDE.local.md`.
4. Clicking `Enforce` on that finding updates the next sync, records a task
   result, and writes `claudeMdExcludes` into
   `dist/preview-devices/rogue_instruction_file/home/workspace-rogue-instruction-file/.claude/settings.local.json`.
5. Optional: enforce the `Preview Rogue MCP` finding and confirm
   `.mcp.json` stays unchanged while `disabledMcpjsonServers` lands in the
   materialized
   `dist/preview-devices/rogue_mcp/home/workspace-rogue-mcp/.claude/settings.local.json`.
