# Tracecat Helm Chart

This chart deploys Tracecat as a single Helm release with optional self-hosted Temporal.

## Deploying the single chart

### 1. Create namespace

```bash
kubectl create namespace tracecat
```

### 2. Choose a secret strategy

You can either supply existing Kubernetes secrets or let this chart render native Secret manifests.

#### Option A: Use existing Kubernetes secrets

```bash
# Required: Tracecat application secrets
kubectl create secret generic tracecat-secrets \
  -n tracecat \
  --from-literal=dbEncryptionKey=YOUR_DB_ENCRYPTION_KEY \
  --from-literal=serviceKey=YOUR_SERVICE_KEY \
  --from-literal=signingSecret=YOUR_SIGNING_SECRET \
  --from-literal=userAuthSecret=YOUR_USER_AUTH_SECRET

# Required: external Postgres credentials
kubectl create secret generic tracecat-postgres-credentials \
  -n tracecat \
  --from-literal=username=YOUR_PG_USER \
  --from-literal=password=YOUR_PG_PASSWORD

# Required for Redis when using K8s secret auth
kubectl create secret generic tracecat-redis-credentials \
  -n tracecat \
  --from-literal=url='redis://:YOUR_REDIS_PASSWORD@YOUR_REDIS_HOST:6379'

# Optional: Temporal Web UI OIDC (self-hosted Temporal only)
kubectl create secret generic temporal-ui-oidc \
  -n tracecat \
  --from-literal=TEMPORAL_AUTH_CLIENT_ID=YOUR_OIDC_CLIENT_ID \
  --from-literal=TEMPORAL_AUTH_CLIENT_SECRET=YOUR_OIDC_CLIENT_SECRET
```

#### Option B: Let the chart render secret templates

Set:

- `secrets.create.tracecat.enabled=true` (required if not using `secrets.existingSecret`)
- `secrets.create.postgres.enabled=true` (required if not using `externalPostgres.auth.existingSecret`)
- `secrets.create.temporalUiOidc.enabled=true` (optional for self-hosted Temporal UI OIDC)
- `temporal.web.additionalEnvSecretName=temporal-ui-oidc` (when enabling Temporal UI OIDC via secret)

When enabled, `secrets.create.*` resources are rendered as `pre-install,pre-upgrade` hooks so they exist before migration hooks run.

### 3. Install with ingress networking (default)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  --set tracecat.auth.superadminEmail=admin@example.com \
  --set ingress.enabled=true \
  --set ingress.host=tracecat.example.com \
  --set secrets.existingSecret=tracecat-secrets \
  --set externalPostgres.host=your-db-host.example.com \
  --set externalPostgres.auth.existingSecret=tracecat-postgres-credentials \
  --set externalRedis.auth.existingSecret=tracecat-redis-credentials
```

### 4. Install with Istio VirtualService networking (ingress disabled)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  --set tracecat.auth.superadminEmail=admin@example.com \
  --set ingress.enabled=false \
  --set urls.publicApp=https://tracecat.example.com \
  --set urls.publicApi=https://tracecat.example.com/api \
  --set secrets.existingSecret=tracecat-secrets \
  --set externalPostgres.host=your-db-host.example.com \
  --set externalPostgres.auth.existingSecret=tracecat-postgres-credentials \
  --set externalRedis.auth.existingSecret=tracecat-redis-credentials \
  --set virtualService.enabled=true \
  --set virtualService.tracecat.enabled=true \
  --set 'virtualService.tracecat.configs[0].name=tracecat' \
  --set 'virtualService.tracecat.configs[0].hosts[0]=tracecat.example.com' \
  --set 'virtualService.tracecat.configs[0].gateways[0]=istio-system/public-gateway'
```

### OrbStack (local development on macOS)

OrbStack provides a lightweight Kubernetes environment for macOS with automatic DNS resolution for `.k8s.orb.local` domains.

**Step 1: Install NGINX ingress controller**

```bash
kubectl apply -f \
  https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml
```

Wait for the ingress controller to be ready:

```bash
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s
```

**Step 2: Complete prerequisites** (create namespace, create secrets, set up external services)

**Step 3: Install Tracecat**

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  -f examples/values-orbstack.yaml
```

**Step 4: Access Tracecat**

Once all pods are running, access the UI at: **http://tracecat.k8s.orb.local**

> **Note**: OrbStack automatically resolves `*.k8s.orb.local` domains to your local Kubernetes cluster. The hostname is configured in `values-orbstack.yaml` via `ingress.host`.

To check pod status:

```bash
kubectl get pods -n tracecat
```

### Minikube (local development)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  -f examples/values-minikube.yaml
```

See `examples/` for complete example configurations.

## Configuration

### Required values

| Parameter | Description |
|-----------|-------------|
| `tracecat.auth.superadminEmail` | Initial admin email (required on first install) |
| `externalPostgres.host` | PostgreSQL hostname |
| `secrets.existingSecret` **or** `secrets.create.tracecat.enabled=true` **or** `externalSecrets.coreSecrets.secretArn` | Required Tracecat core secret source |
| `externalPostgres.auth.existingSecret` **or** `externalPostgres.auth.secretArn` **or** `secrets.create.postgres.enabled=true` | Required Postgres credential source |
| `externalRedis.auth.existingSecret` **or** `externalRedis.auth.secretArn` | Required Redis credential source |

### Secret templates (native K8s Secrets)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `secrets.create.tracecat.enabled` | `false` | Render `tracecat-secrets` |
| `secrets.create.tracecat.name` | `tracecat-secrets` | Name of Tracecat core secret |
| `secrets.create.postgres.enabled` | `false` | Render `tracecat-postgres-credentials` |
| `secrets.create.postgres.name` | `tracecat-postgres-credentials` | Name of Postgres secret |
| `secrets.create.temporalUiOidc.enabled` | `false` | Render `temporal-ui-oidc` for self-hosted Temporal UI |
| `secrets.create.temporalUiOidc.name` | `temporal-ui-oidc` | Name of Temporal UI OIDC secret |

### Ingress

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ingress.enabled` | `true` | Enable ingress |
| `ingress.className` | `""` | Ingress class name (nginx, alb, etc.) |
| `ingress.host` | `tracecat.example.com` | Hostname |
| `ingress.annotations` | `{}` | Ingress annotations |
| `ingress.split` | `false` | Split ingress into separate UI/API resources |
| `ingress.ui.annotations` | `{}` | UI ingress annotations (merged with `ingress.annotations`) |
| `ingress.api.annotations` | `{}` | API ingress annotations (merged with `ingress.annotations`) |
| `ingress.tls` | `[]` | TLS configuration |

### VirtualService (Istio)

Use this when ingress is disabled and traffic is managed by Istio gateways.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `virtualService.enabled` | `false` | Enable VirtualService rendering |
| `virtualService.apiVersion` | `networking.istio.io/v1beta1` | Istio API version |
| `virtualService.tracecat.enabled` | `true` | Render Tracecat UI/API VirtualServices |
| `virtualService.tracecat.configs` | `[]` | Required list of `{name, hosts, gateways}` entries |
| `virtualService.webhooks.enabled` | `false` | Render webhook-only VirtualServices |
| `virtualService.webhooks.timeout` | `5s` | Timeout for webhook routes |
| `virtualService.webhooks.configs` | `[]` | Required list of `{name, hosts, gateways}` entries |
| `virtualService.temporal.enabled` | `false` | Render Temporal Web UI VirtualServices |
| `virtualService.temporal.configs` | `[]` | Required list of `{name, hosts, gateways}` entries (self-hosted Temporal only) |

Example:

```yaml
ingress:
  enabled: false

virtualService:
  enabled: true
  tracecat:
    enabled: true
    configs:
      - name: tracecat
        hosts:
          - tracecat.example.com
        gateways:
          - istio-system/public-gateway
  webhooks:
    enabled: true
    configs:
      - name: tracecat-webhooks
        hosts:
          - webhooks.example.com
        gateways:
          - istio-system/public-gateway
  temporal:
    enabled: true
    configs:
      - name: tracecat-temporal
        hosts:
          - temporal.example.com
        gateways:
          - istio-system/private-gateway
```

### Public URLs

Public URLs are auto-generated from `ingress.host` and TLS configuration:

- If `ingress.tls` is configured: `https://<host>`
- Otherwise: `http://<host>`

For deployments where TLS is terminated externally (e.g., AWS ALB with ACM certificates), set URLs explicitly:

```yaml
urls:
  publicApp: https://tracecat.example.com
  publicApi: https://tracecat.example.com/api
```

### Core settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tracecat.appEnv` | `production` | Application environment |
| `tracecat.logLevel` | `INFO` | Log level for backend services |
| `tracecat.allowOrigins` | `""` | CORS allowed origins |
| `tracecat.featureFlags` | `""` | Core feature flags (comma-separated) |
| `enterprise.featureFlags` | `""` | Enterprise-only flags appended to `tracecat.featureFlags` |
| `enterprise.multiTenant` | `false` | Enable enterprise multi-tenant mode |

### Service replicas and resources

| Parameter | Default | Description |
|-----------|---------|-------------|
| `api.replicas` | `1` | API service replicas |
| `worker.replicas` | `1` | Temporal worker replicas |
| `executor.replicas` | `1` | Action executor replicas |
| `agentExecutor.replicas` | `1` | Agent executor replicas |
| `ui.replicas` | `1` | UI service replicas |

Each service also supports `resources.requests.cpu`, `resources.requests.memory`, `resources.limits.cpu`, and `resources.limits.memory`. See `values.yaml` for defaults.

### Sandbox (nsjail)

By default the executor runs with nsjail enabled, which requires privileged pods, `SYS_ADMIN`, and an unconfined seccomp profile. If your cluster disallows this (or for local development), disable nsjail:

```yaml
tracecat:
  sandbox:
    disableNsjail: true
```

### Pod scheduling

Global scheduling defaults applied to all Tracecat workloads:

```yaml
scheduling:
  nodeSelector: {}
  affinity: {}
  topologySpreadConstraints: []
  tolerations: []
```

### PostgreSQL

External PostgreSQL is required. There is no bundled database.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `externalPostgres.host` | | PostgreSQL hostname (required) |
| `externalPostgres.port` | `5432` | PostgreSQL port |
| `externalPostgres.database` | `tracecat` | Database name |
| `externalPostgres.sslMode` | `prefer` | SSL mode (`disable`, `prefer`, `require`, `verify-ca`) |
| `externalPostgres.auth.existingSecret` | | K8s secret with `username` and `password` keys |
| `externalPostgres.auth.secretArn` | | AWS Secrets Manager ARN (JSON with `password` and optional `username`) |
| `externalPostgres.auth.username` | `""` | Explicit username when using `secretArn` |
| `externalPostgres.tls.verifyCA` | `false` | Enable TLS CA verification |
| `externalPostgres.tls.caCert` | `""` | PEM-encoded CA certificate for server verification |

You must provide either `auth.existingSecret` or `auth.secretArn` (or both). When both are set, the chart exports `TRACECAT__DB_PASS__ARN` and `TRACECAT__DB_PASS` together.

For AWS RDS with TLS verification:

```yaml
externalPostgres:
  host: my-db.xxxx.us-east-1.rds.amazonaws.com
  sslMode: require
  auth:
    existingSecret: tracecat-postgres-credentials
  tls:
    verifyCA: true
    caCert: |
      -----BEGIN CERTIFICATE-----
      ...RDS CA bundle...
      -----END CERTIFICATE-----
```

### Redis

External Redis is required. There is no bundled Redis.

| Parameter | Description |
|-----------|-------------|
| `externalRedis.auth.existingSecret` | K8s secret with `url` key (e.g., `redis://:password@host:6379`) |
| `externalRedis.auth.secretArn` | AWS Secrets Manager ARN containing the raw Redis URL |

You must provide either `auth.existingSecret` or `auth.secretArn`. For Redis with TLS, use `rediss://:password@host:port`.

### S3

External S3-compatible storage is used for blob storage. Credentials are optional when using IRSA/IAM roles.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `externalS3.endpoint` | `""` | Custom S3-compatible endpoint (leave empty for AWS S3) |
| `externalS3.region` | `""` | AWS region |
| `externalS3.auth.existingSecret` | | K8s secret with `accessKeyId` and `secretAccessKey` (optional for IRSA) |
| `tracecat.blobStorage.endpoint` | `""` | Override endpoint (takes precedence over `externalS3.endpoint`) |
| `tracecat.blobStorage.buckets.attachments` | `""` | Attachments bucket name |
| `tracecat.blobStorage.buckets.registry` | `""` | Registry bucket name |

### Temporal

The chart supports two Temporal modes: a self-hosted subchart or an external cluster.

#### Self-hosted (default)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `temporal.enabled` | `true` | Deploy Temporal as a subchart |
| `temporal.clusterQueue` | `tracecat-task-queue` | Temporal task queue for Tracecat workers |
| `temporal.web.additionalEnvSecretName` | `""` | Optional secret for Temporal Web UI env vars (set to `temporal-ui-oidc` when using OIDC) |
| `temporal.server.config.persistence.datastores.default.sql.existingSecret` | `tracecat-postgres-credentials` | Secret used for Temporal default store SQL password |
| `temporal.server.config.persistence.datastores.visibility.sql.existingSecret` | `tracecat-postgres-credentials` | Secret used for Temporal visibility store SQL password |
| `temporal.server.archival.history.state` | `disabled` | Cluster-level history archival state (`enabled` to turn on archival) |
| `temporal.server.archival.visibility.state` | `disabled` | Cluster-level visibility archival state (`enabled` to turn on archival) |
| `temporal.server.namespaceDefaults.archival.history.URI` | `""` | Default namespace history archival URI (for example `s3://bucket/temporal-history`) |
| `temporal.server.namespaceDefaults.archival.visibility.URI` | `""` | Default namespace visibility archival URI (for example `s3://bucket/temporal-visibility`) |

If you override `secrets.create.postgres.name`, also override both Temporal datastore `existingSecret` values to the same secret name.

When using the self-hosted subchart, you must configure `temporal.server.config.persistence` to point to your external database. The `temporal` and `temporal_visibility` databases must already exist. See `terraform/aws/modules/eks/helm.tf` for a production example.

For Temporal chart `1.0.0-rc.1`, archival is rendered from `server.archival` and `server.namespaceDefaults` in `temporal/templates/server-configmap.yaml` (the bundled `values/values.archival.s3.yaml` still matches those active keys).

The chart runs a Helm hook job after install/upgrade to reconcile the `default` namespace settings (retention + archival when configured) and the search attributes in `tracecat.temporal.searchAttributes`.

#### External

Set `temporal.enabled=false` and configure `externalTemporal`:

| Parameter | Description |
|-----------|-------------|
| `externalTemporal.enabled` | Enable external Temporal mode |
| `externalTemporal.clusterUrl` | Temporal frontend `host:port` |
| `externalTemporal.clusterNamespace` | Temporal namespace |
| `externalTemporal.clusterQueue` | Temporal task queue for Tracecat workers |
| `externalTemporal.auth.existingSecret` | K8s secret with `apiKey` key |
| `externalTemporal.auth.secretArn` | AWS Secrets Manager ARN with API key |

When using an external cluster, you must create the namespace and search attributes yourself.

### Service account (IRSA)

Shared service account used by `api`, `worker`, `executor`, and `agentExecutor` pods.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `serviceAccount.create` | `true` | Create the service account |
| `serviceAccount.name` | `""` | Override the service account name |
| `serviceAccount.annotations` | `{}` | Annotations (e.g., `eks.amazonaws.com/role-arn`) |

### Reloader

[Stakater Reloader](https://github.com/stakater/Reloader) annotations are enabled by default (`reloader.enabled: true`). Pods automatically restart when their referenced secrets are updated. This is useful for handling AWS Secrets Manager rotation (e.g., RDS password rotation).

### External Secrets Operator (ESO)

For GitOps workflows, ESO syncs secrets from AWS Secrets Manager into Kubernetes automatically, avoiding manual secret creation.

**Prerequisites:**

1. Install External Secrets Operator:
   ```bash
   helm repo add external-secrets https://charts.external-secrets.io
   helm install external-secrets external-secrets/external-secrets \
     -n external-secrets --create-namespace
   ```

2. Create a `ClusterSecretStore` with IRSA authentication (typically done by platform team or Terraform).

3. Store secrets in AWS Secrets Manager with expected formats:
   - Core secrets: JSON `{ "dbEncryptionKey": "...", "serviceKey": "...", "signingSecret": "...", "userAuthSecret": "..." }`
   - PostgreSQL: JSON `{ "username": "...", "password": "..." }`
   - Redis: raw URL string (e.g., `rediss://:password@host:port`)
   - Temporal: raw API key string

**Configuration:**

```yaml
secrets:
  existingSecret: ""  # Leave empty - ESO creates it

externalSecrets:
  enabled: true
  refreshInterval: "1m"
  clusterSecretStoreRef: "your-cluster-store-name"

  coreSecrets:
    enabled: true
    secretArn: "arn:aws:secretsmanager:us-east-1:123456789:secret:tracecat/core-AbCdEf"
    targetSecretName: "tracecat-secrets"

  postgres:
    enabled: true
    secretArn: "arn:aws:secretsmanager:us-east-1:123456789:secret:rds!db-xxxx"
    targetSecretName: "tracecat-postgres-credentials"

  redis:
    enabled: true
    secretArn: "arn:aws:secretsmanager:us-east-1:123456789:secret:tracecat/redis-xxxx"
    targetSecretName: "tracecat-redis-credentials"
```

| Parameter | Description |
|-----------|-------------|
| `externalSecrets.enabled` | Enable ESO integration |
| `externalSecrets.refreshInterval` | How often to sync secrets (default: `1m`) |
| `externalSecrets.clusterSecretStoreRef` | Name of existing ClusterSecretStore |
| `externalSecrets.coreSecrets.secretArn` | ARN for core Tracecat secrets |
| `externalSecrets.postgres.secretArn` | ARN for PostgreSQL credentials |
| `externalSecrets.redis.secretArn` | ARN for Redis URL |
| `externalSecrets.temporal.secretArn` | ARN for Temporal API key |

For AWS production, see `terraform/aws/modules/eks/helm.tf`.

## Upgrading

```bash
helm upgrade tracecat ./tracecat -n tracecat -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall tracecat -n tracecat
```

**Note**: This will not delete PersistentVolumeClaims or secrets. Delete them manually if needed.
