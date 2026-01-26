# Tracecat Helm Chart

This Helm chart deploys Tracecat on Kubernetes with support for both internal subcharts (CloudNativePG, Valkey, MinIO, Temporal) and external managed services (RDS, ElastiCache, S3).

## Prerequisites

### 1. Install CloudNativePG operator (required for internal PostgreSQL)

If using the internal PostgreSQL subchart (`postgres.enabled=true`), you must install the CloudNativePG operator first. The helm chart includes a `cluster` subchart that creates a PostgreSQL Cluster resource, but the operator must be installed separately to manage it.

```bash
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/releases/cnpg-1.25.1.yaml
```

Verify the operator is running:

```bash
kubectl get pods -n cnpg-system
```

> **Note**: Skip this step if using external PostgreSQL (`externalPostgres.enabled=true`).

### 2. Create namespace

```bash
kubectl create namespace tracecat
```

### 3. Create core credentials secret

```bash
# Generate credentials locally
export DB_ENCRYPTION_KEY=$(openssl rand 32 | base64 | tr -d '\n' | tr '+/' '-_')
export SERVICE_KEY=$(openssl rand -hex 32)
export SIGNING_SECRET=$(openssl rand -hex 32)
export USER_AUTH_SECRET=$(openssl rand -hex 32)

# Create core credentials secret
kubectl create secret generic tracecat-secrets \
  -n tracecat \
  --from-literal=dbEncryptionKey=$DB_ENCRYPTION_KEY \
  --from-literal=serviceKey=$SERVICE_KEY \
  --from-literal=signingSecret=$SIGNING_SECRET \
  --from-literal=userAuthSecret=$USER_AUTH_SECRET \
  --dry-run=client -o yaml | kubectl apply -f -
```

## Installation

### Basic installation (internal services)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  --set secrets.existingSecret=tracecat-secrets \
  --set tracecat.auth.superadminEmail=admin@example.com \
  --set ingress.host=tracecat.example.com
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

**Step 2: Complete prerequisites 1-3** (install CNPG operator, create namespace, create secrets)

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
| `secrets.existingSecret` | Name of K8s secret with core credentials |
| `tracecat.auth.superadminEmail` | Initial admin email (required on first install) |

### Service replicas

| Parameter | Default | Description |
|-----------|---------|-------------|
| `api.replicas` | 2 | API service replicas |
| `worker.replicas` | 4 | Temporal worker replicas |
| `executor.replicas` | 2 | Action executor replicas |
| `ui.replicas` | 1 | UI service replicas |

### Ingress

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ingress.enabled` | true | Enable ingress |
| `ingress.className` | "" | Ingress class name (nginx, alb, etc.) |
| `ingress.host` | tracecat.example.com | Hostname |
| `ingress.annotations` | {} | Ingress annotations |
| `ingress.tls` | [] | TLS configuration |

### Public URLs

Public URLs are auto-generated based on `ingress.host` and TLS configuration:

- If `ingress.tls` is configured → `https://`
- If `ingress.tls` is not set → `http://`

For deployments where TLS is terminated externally (e.g., AWS ALB with ACM certificates), set URLs explicitly:

```yaml
urls:
  publicApp: https://tracecat.example.com
  publicApi: https://tracecat.example.com/api
```

### Core settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tracecat.logLevel` | INFO | LOG_LEVEL for backend services |
| `tracecat.featureFlags` | `""` | Core flags (comma-separated) |
| `enterprise.featureFlags` | "" | Enterprise-only flags appended to `tracecat.featureFlags` |

### Sandbox (nsjail)

By default the executor runs with nsjail enabled, which requires privileged pods, `SYS_ADMIN`, and an unconfined seccomp profile. If your cluster disallows this (or for local development), disable nsjail:

```yaml
tracecat:
  sandbox:
    disableNsjail: true
```

### Internal services (subcharts)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `postgres.enabled` | true | Enable CloudNativePG PostgreSQL |
| `redis.enabled` | true | Enable Valkey (Redis) |
| `minio.enabled` | true | Enable MinIO for blob storage |
| `temporal.enabled` | true | Enable Temporal server |

### Temporal (internal subchart)

The chart runs a Helm hook job after install/upgrade to create the `default` namespace and the search attributes in `tracecat.temporal.searchAttributes`. Workers wait for this setup before starting.

For external Temporal (`temporal.enabled=false`), you must create the namespace and search attributes yourself.

When using the internal Temporal subchart with an external Postgres (for example, RDS), override `temporal.server.config.persistence` as shown in `examples/values-aws-alb.yaml`, and ensure the `temporal` and `temporal_visibility` databases already exist. For internal Postgres, the chart bootstraps these databases on first cluster creation via `postgres.cluster.initdb`.

### External services

Configure these when using managed services (RDS, ElastiCache, etc.):

| Parameter | Description |
|-----------|-------------|
| `externalPostgres.enabled` | Use external PostgreSQL |
| `externalPostgres.host` | PostgreSQL hostname |
| `externalPostgres.auth.existingSecret` | Secret with `username` and `password` |
| `externalPostgres.auth.secretArn` | AWS Secrets Manager ARN for `password` (and optional `username`) |
| `externalPostgres.auth.username` | Optional username when using `secretArn` |
| `externalRedis.enabled` | Use external Redis |
| `externalRedis.auth.existingSecret` | Secret with `url` |
| `externalRedis.auth.secretArn` | AWS Secrets Manager ARN with `url` |
| `externalS3.enabled` | Use external S3 |
| `externalS3.endpoint` | Custom endpoint for S3-compatible storage (leave empty for AWS S3) |
| `externalS3.region` | AWS region for S3 |
| `externalS3.auth.existingSecret` | Secret with `accessKeyId` and `secretAccessKey` (optional for IRSA) |
| `externalTemporal.enabled` | Use external Temporal |
| `externalTemporal.clusterUrl` | Temporal frontend host:port |
| `externalTemporal.auth.existingSecret` | Secret with `apiKey` |
| `externalTemporal.auth.secretArn` | AWS Secrets Manager ARN with `apiKey` |

Notes:
- Postgres Secrets Manager values should be JSON with `username` and `password` (or set `externalPostgres.auth.username` and store just the password as the secret string).
- Redis and Temporal Secrets Manager values should be raw strings (URL / API key). For Redis with TLS+auth, use `rediss://:password@host:port`.

### Service account (IRSA)

Shared service account used by `api`, `worker`, and `executor` pods.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `serviceAccount.create` | true | Create the service account |
| `serviceAccount.name` | "" | Override the service account name |
| `serviceAccount.annotations` | {} | Annotations (e.g., `eks.amazonaws.com/role-arn`) |

### External Secrets Operator (ESO) integration

For GitOps workflows, you can use ESO to sync secrets from AWS Secrets Manager into Kubernetes automatically. This avoids storing secrets in Terraform state or manually creating K8s secrets.

**Prerequisites:**

1. Install External Secrets Operator:
   ```bash
   helm repo add external-secrets https://charts.external-secrets.io
   helm install external-secrets external-secrets/external-secrets \
     -n external-secrets --create-namespace
   ```

2. Create a `ClusterSecretStore` with IRSA authentication (typically done by platform team or Terraform).

3. Store secrets in AWS Secrets Manager with expected JSON format:
   ```json
   {
     "dbEncryptionKey": "<url-safe-base64-32-bytes>",
     "serviceKey": "<hex-64-chars>",
     "signingSecret": "<hex-64-chars>",
     "userAuthSecret": "<hex-64-chars>"
   }
   ```

**Configuration:**

```yaml
# Leave existingSecret empty - ESO will create it
secrets:
  existingSecret: ""

externalSecrets:
  enabled: true
  refreshInterval: "1h"
  clusterSecretStoreRef: "your-cluster-store-name"

  coreSecrets:
    enabled: true
    secretArn: "arn:aws:secretsmanager:us-east-1:123456789:secret:tracecat/core-AbCdEf"
    targetSecretName: "tracecat-secrets"

  # Optional: PostgreSQL, Redis, Temporal credentials
  postgres:
    enabled: true
    secretArn: "arn:aws:secretsmanager:us-east-1:123456789:secret:rds!db-xxxx"
    targetSecretName: "tracecat-postgres-credentials"
```

See `examples/values-aws.yaml` for a complete example.

| Parameter | Description |
|-----------|-------------|
| `externalSecrets.enabled` | Enable ESO integration |
| `externalSecrets.refreshInterval` | How often to sync secrets (default: 1h) |
| `externalSecrets.clusterSecretStoreRef` | Name of existing ClusterSecretStore |
| `externalSecrets.coreSecrets.secretArn` | AWS Secrets Manager ARN for core secrets |
| `externalSecrets.postgres.secretArn` | ARN for PostgreSQL credentials (JSON with `username`, `password`) |
| `externalSecrets.redis.secretArn` | ARN for Redis URL (raw string, e.g. `rediss://:password@host:port`) |
| `externalSecrets.temporal.secretArn` | ARN for Temporal API key (raw string) |

## Upgrading

```bash
helm upgrade tracecat ./tracecat -n tracecat -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall tracecat -n tracecat
```

**Note**: This will not delete PersistentVolumeClaims or secrets. Delete them manually if needed.
