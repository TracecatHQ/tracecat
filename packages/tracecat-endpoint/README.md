# tracecat-endpoint

Local Go module for Tracecat Endpoint, the installed local endpoint component for
AI-SPM.

## Development

```bash
go vet ./...
go build -o dist/tracecat-endpoint ./cmd/tracecat-endpoint
```

## Layout

- `cmd/tracecat-endpoint`: CLI entrypoint
- `internal/version`: Build metadata for the binary
- `testdata/`: Reserved for fixture payloads and local tests

## Future Direction

Tracecat Endpoint is intended to evolve into a local AI-SPM endpoint component
that:

- connects outbound to Tracecat over HTTPS
- polls for desired state and remediation updates
- reconciles local AI tool posture within a few minutes
