# Tracecat Helm Chart

This Helm chart deploys Tracecat on Kubernetes with support for both internal subcharts (CloudNativePG, Valkey, MinIO, Temporal) and external managed services (RDS, ElastiCache, S3).

## Prerequisites

### 1. Create Namespace

```bash
kubectl create namespace tracecat
```

### 2. Create Core Credentials Secret

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

### 3. (Optional) Create OAuth Credentials Secret

If using Google OAuth authentication:

```bash
kubectl create secret generic tracecat-oauth \
  -n tracecat \
  --from-literal=oauthClientId=YOUR_CLIENT_ID \
  --from-literal=oauthClientSecret=YOUR_CLIENT_SECRET \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4. (AWS Only) Create External Service Secrets

For RDS PostgreSQL:

```bash
kubectl create secret generic tracecat-rds-credentials \
  -n tracecat \
  --from-literal=username=YOUR_RDS_USERNAME \
  --from-literal=password=YOUR_RDS_PASSWORD \
  --dry-run=client -o yaml | kubectl apply -f -
```

For ElastiCache Redis:

```bash
kubectl create secret generic tracecat-redis-credentials \
  -n tracecat \
  --from-literal=url=redis://YOUR_ELASTICACHE_ENDPOINT:6379 \
  --dry-run=client -o yaml | kubectl apply -f -
```

For S3 (only if not using IRSA/IAM roles):

```bash
kubectl create secret generic tracecat-s3-credentials \
  -n tracecat \
  --from-literal=accessKeyId=YOUR_ACCESS_KEY \
  --from-literal=secretAccessKey=YOUR_SECRET_KEY \
  --dry-run=client -o yaml | kubectl apply -f -
```

## Installation

### Basic Installation (Internal Services)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  --set secrets.existingSecret=tracecat-secrets \
  --set tracecat.auth.superadminEmail=admin@example.com \
  --set ingress.host=tracecat.example.com
```

### Minikube (Local Development)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  -f ../examples/values-minikube.yaml
```

### nginx-ingress (Internal Services)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  -f ../examples/values-nginx-ingress.yaml
```

### AWS ALB (External Services)

```bash
helm install tracecat ./tracecat \
  -n tracecat \
  -f ../examples/values-aws-alb.yaml
```

See `../examples/` for complete example configurations.

## Configuration

### Required Values

| Parameter | Description |
|-----------|-------------|
| `secrets.existingSecret` | Name of K8s secret with core credentials |
| `tracecat.auth.superadminEmail` | Initial admin email (required on first install) |

### Service Replicas

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

### Internal Services (Subcharts)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `postgres.enabled` | true | Enable CloudNativePG PostgreSQL |
| `redis.enabled` | true | Enable Valkey (Redis) |
| `minio.enabled` | true | Enable MinIO for blob storage |
| `temporal.enabled` | true | Enable Temporal server |

### External Services

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
- Redis and Temporal Secrets Manager values should be raw strings (URL / API key).

### Blob Storage URLs

- `urls.publicS3` is only used to rewrite presigned URLs (defaults to `/s3` only when MinIO is enabled).
- For external S3, leave `urls.publicS3` empty unless you need to rewrite URLs through a proxy.

## Architecture

The chart deploys:

- **API Service** (Deployment + Service): FastAPI backend on port 8000
- **Worker** (Deployment): Temporal workflow worker
- **Executor** (Deployment): Action execution engine with nsjail sandbox
- **UI** (Deployment + Service): Next.js frontend on port 3000
- **Ingress**: Routes `/api/*` → API, `/s3/*` → MinIO (only when enabled), `/` → UI

## Upgrading

```bash
helm upgrade tracecat ./tracecat -n tracecat -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall tracecat -n tracecat
```

**Note**: This will not delete PersistentVolumeClaims or secrets. Delete them manually if needed.
