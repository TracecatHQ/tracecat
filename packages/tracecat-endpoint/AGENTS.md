# Tracecat Endpoint notes

Use this file for work in `packages/tracecat-endpoint/`.

## Purpose

- This package contains the Go-based local endpoint component for Tracecat.
- Keep it isolated from the Python and frontend workspaces except through
  documented APIs and wire contracts.
- Optimize for a small local development binary first; do not add release
  packaging here yet.

## Toolchain

- The module is a standalone nested Go module with its own `go.mod`.
- Use the toolchain pinned in `go.mod`.
- Do not add a repo-root `go.work`.
- Keep `CGO_ENABLED=0` compatibility unless a change explicitly requires CGO.

## Layout

- `cmd/tracecat-endpoint/`: CLI entrypoint only.
- `internal/`: implementation packages.
- `testdata/`: fixture payloads and sample inputs for tests.
- `dist/`: local build output only; do not check it in.

## Commands

Run commands from `packages/tracecat-endpoint/` unless noted otherwise.

```bash
go vet ./...
go build -o dist/tracecat-endpoint ./cmd/tracecat-endpoint
```

Format Go code with:

```bash
gofmt -w .
```

## Coding rules

- Prefer the Go standard library first; add dependencies sparingly.
- Keep package names lowercase and idiomatic.
- Prefer explicit types over `any` unless dynamic data is required.
- Keep functions small and direct; favor guard clauses over deep nesting.
- Return wrapped errors with useful context.
- Keep CLI wiring in `cmd/` thin; put behavior in `internal/`.
- Do not introduce framework-heavy abstractions unless duplication justifies it.

## Testing

- Test files must end in `_test.go`.
- Prefer table-driven unit tests.
- Keep most tests in the same package when unexported behavior matters.
- Use `testdata/` for JSON fixtures and sample client payloads.
- Add or update tests when relay behavior, event normalization, signing, or
  install logic changes.

## Boundaries

- Do not add Apple notarization, installer packaging, or release automation in
  this package yet.
- Do not add Dockerized Go build flows yet.
- Do not couple this module to the Python `uv` workspace.
- Keep local machine integrations optional and well-contained.

## Product direction

- Treat this component as Tracecat Endpoint in both code and docs.
- The intended v1 control model is outbound HTTPS plus periodic desired-state
  reconciliation.
- Enforcement may be added later, but do not implement daemonization, polling,
  or local settings mutation in the initial hello-world scaffold.
