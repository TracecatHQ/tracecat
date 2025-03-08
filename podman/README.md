# Container Runner Service

This service provides a dedicated container for running podman containers in a sandboxed environment. It exposes a podman API that can be used by the executor service to run containers securely.

## Architecture

The container-runner service is a dedicated container that runs the podman daemon and exposes its API over TCP. The executor service connects to this API to run containers securely.

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
│             │     │                 │     │                 │
│    API      │────▶│    Executor     │────▶│ Container Runner│
│             │     │                 │     │                 │
└─────────────┘     └─────────────────┘     └─────────────────┘
                                                     │
                                                     ▼
                                            ┌─────────────────┐
                                            │                 │
                                            │   Containers    │
                                            │                 │
                                            └─────────────────┘
```

## Configuration

The container-runner service is configured in the docker-compose.yml file. It uses a custom Dockerfile that sets up the podman daemon with secure defaults.

### Environment Variables

- `TRACECAT__PODMAN_URI`: The URI of the podman API. This is set to `tcp://container-runner:8080` in the executor service.

## Security

The container-runner service is configured with secure defaults:

- It runs in a privileged container to allow it to run containers
- It uses a custom seccomp profile to restrict system calls
- It uses a custom storage configuration to restrict access to the host filesystem
- It uses a custom containers configuration to restrict container capabilities

## Usage

The executor service connects to the container-runner service using the `TRACECAT__PODMAN_URI` environment variable. The container-runner service exposes its API on port 8080.

### Example

```python
from tracecat.sandbox.podman import run_podman_container, PodmanNetwork

result = run_podman_container(
    image="alpine:latest",
    command=["echo", "Hello, World!"],
    network=PodmanNetwork.NONE
)
print(result.output)  # Hello, World!
```

## Troubleshooting

If you encounter issues with the container-runner service, you can check the logs:

```bash
docker-compose logs container-runner
```

You can also check if the podman API is accessible:

```bash
curl http://localhost:8080/v1.40/version
```

If you need to rebuild the container-runner service:

```bash
docker-compose build container-runner
docker-compose up -d container-runner
```
