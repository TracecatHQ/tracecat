# tracecatd

Local Go module for `tracecatd`, the Tracecat Endpoint daemon for AI SPM.

## Development

```bash
go vet ./...
go build -o dist/tracecatd ./cmd/tracecatd
go test ./...
```

## Layout

- `cmd/tracecatd`: CLI entrypoint
- `internal/cli`: command parsing and top-level workflows
- `internal/state`: local `~/.tracecatd/state.json` persistence
- `internal/spmapi`: sync transport types and HTTP client
- `internal/runner`: bootstrap, inventory, sync loop, and reconciliation flow
- `internal/inventory`: Claude Code config and instruction-file inventory
- `internal/tasks`: Claude Code local reconciliation executor
- `internal/launchagent`: per-user LaunchAgent install and uninstall
- `internal/version`: Build metadata for the binary
- `e2e/`: preview-device end-to-end wrapper, compose stack, and runtime image
- `testdata/`: fixture payloads, sample inputs, and local tests

## Commands

`tracecatd` currently supports the ENG-1360 shell:

- `tracecatd run`
- `tracecatd run --once`
- `tracecatd install`
- `tracecatd uninstall`

Bootstrap flags for `run` and `install`:

- `--server-url`
- `--state-dir`
- `--home-dir`
- `--endpoint-id`
- `--enrollment-token`

## Current Behavior

- stores state in `~/.tracecatd/state.json` by default
- redeems the enrollment token for a long-lived endpoint secret on first sync
- sends endpoint metadata plus queued `pending_task_results` to `POST /spm/endpoints/{endpoint_id}/sync`
- inventories user and project Claude surfaces discovered from Claude-managed directory state
- records Claude instruction files, MCP servers, hooks, skills, subagents, permissions, sandbox config, and workspace access assets
- preserves missing or malformed-file parse state in asset metadata and evidence instead of failing sync
- reconciles writable Claude config surfaces for MCP disable, instruction-file exclusion, directory revocation, permission and sandbox config, hooks, and skills
- immediately performs one follow-up sync to flush queued task results
- installs a per-user LaunchAgent at `~/Library/LaunchAgents/com.tracecat.tracecatd.plist`

## Preview Devices QA

Use the preview-device harness to run long-lived synthetic Claude endpoints
against a normal local Tracecat stack started by `just cluster up -d`.

```bash
just preview-devices up -d
just preview-devices ps
just preview-devices logs
just preview-devices down
```

The harness keeps immutable tracked scenarios under `e2e/scenarios/`, copies them
into `dist/preview-devices/` before each run, and preserves per-device
`state.json` so endpoint identity and queued task results survive restarts. See
[`e2e/README.md`](./e2e/README.md) for the topology, runtime layout, and manual QA
flow. The preview wrapper cross-builds `dist/tracecatd` for Linux before
starting the containerized synthetic endpoints.
