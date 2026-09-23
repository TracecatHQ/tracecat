# Bitbucket Data Center development mock

This mock serves Data Center REST responses and a real bare Git repository over
HTTPS. It exercises transport, authentication, pagination, commits, and pull
request creation without a Data Center license. It does not emulate the full
Atlassian product or prove compatibility with a deployed Data Center version.

Run from the repository root:

```bash
uv run python -m tests.support.bitbucket_data_center_mock --directory /tmp/bitbucket-dc --port 8443
```

Configure the Data Center provider with:

- Instance URL: `https://localhost:8443/bitbucket`
- HTTP access token: `synthetic-data-center-dev-token`
- Workspace repository: `git+ssh://git@localhost/DEMO/sync.git`

Start the API process with `SSL_CERT_FILE=/tmp/bitbucket-dc/localhost.crt` so
HTTPX and Git trust the generated development certificate. The API and mock
must run in the same network namespace: a container's localhost is not the host.
The mock binds loopback only. Do not disable TLS verification or use real tokens.

The repository persists under the chosen directory. PR metadata lasts for the
server process. Certificates expire after two days; use a new directory to
regenerate the development certificate. Stop the mock with Ctrl-C.

Run automated round-trip coverage with your local cluster's database, Redis,
and MinIO ports:

```bash
PG_PORT=5432 REDIS_PORT=6379 MINIO_PORT=9000 uv run pytest tests/unit/test_bitbucket_data_center_vcs.py -q
```

Tests provision an ephemeral mock, trust its CA, and clean it up automatically.
They use real HTTPS Git fetch/push and real workspace import/export services;
only the Data Center REST surface and stored credential lookup are mocked.
