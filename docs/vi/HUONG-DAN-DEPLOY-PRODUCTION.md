# HƯỚNG DẪN DEPLOYMENT PRODUCTION - TRACECAT SOAR

## Mục lục
1. [Frontend Customization](#1-frontend-customization)
2. [Docker Packaging](#2-docker-packaging)
3. [Kubernetes Deployment](#3-kubernetes-deployment)
4. [Production Configuration](#4-production-configuration)
5. [Backup và Recovery](#5-backup-và-recovery)
6. [Monitoring và Alerting](#6-monitoring-và-alerting)
7. [Security Hardening](#7-security-hardening)
8. [Performance Optimization](#8-performance-optimization)

---

## 1. Frontend Customization

### 1.1. Branding

**Tạo branding config**:

```json
// config/branding.json
{
  "company": {
    "name": "Your Company SOAR",
    "logo": "/branding/logo.svg",
    "favicon": "/branding/favicon.ico",
    "primaryColor": "#1a56db",
    "secondaryColor": "#0ea5e9"
  },
  "features": {
    "showPoweredByTracecat": false,
    "customFooter": "© 2025 Your Company. All rights reserved.",
    "supportEmail": "support@yourcompany.com"
  },
  "navigation": {
    "hideRegistry": false,
    "customMenuItems": [
      {
        "label": "Documentation",
        "url": "https://docs.yourcompany.com/soar"
      },
      {
        "label": "Support",
        "url": "https://support.yourcompany.com"
      }
    ]
  }
}
```

**Apply branding**:

```typescript
// frontend/src/lib/branding.ts

import branding from "@/config/branding.json"

export function getBranding() {
  return branding
}

export function getCompanyName() {
  return branding.company.name
}

export function getPrimaryColor() {
  return branding.company.primaryColor
}
```

```typescript
// frontend/src/app/layout.tsx

import { getBranding } from "@/lib/branding"

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const branding = getBranding()

  return (
    <html lang="en">
      <head>
        <title>{branding.company.name}</title>
        <link rel="icon" href={branding.company.favicon} />
        <style>{`
          :root {
            --primary: ${branding.company.primaryColor};
            --secondary: ${branding.company.secondaryColor};
          }
        `}</style>
      </head>
      <body>{children}</body>
    </html>
  )
}
```

### 1.2. Custom Pages

**Example: Custom Dashboard**

```typescript
// frontend/src/app/workspaces/[workspaceId]/dashboard/page.tsx

"use client"

import { useQuery } from "@tanstack/react-query"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { BarChart } from "@/components/charts/bar-chart"

export default function CustomDashboard() {
  // Fetch metrics
  const { data: metrics } = useQuery({
    queryKey: ["dashboard-metrics"],
    queryFn: async () => {
      const response = await fetch("/api/custom/metrics")
      return response.json()
    },
  })

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-3xl font-bold">Security Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Active Cases */}
        <Card>
          <CardHeader>
            <CardTitle>Active Cases</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-4xl font-bold">
              {metrics?.activeCases || 0}
            </p>
          </CardContent>
        </Card>

        {/* Workflows Run Today */}
        <Card>
          <CardHeader>
            <CardTitle>Workflows Today</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-4xl font-bold">
              {metrics?.workflowsToday || 0}
            </p>
          </CardContent>
        </Card>

        {/* Critical Alerts */}
        <Card>
          <CardHeader>
            <CardTitle>Critical Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-4xl font-bold text-red-600">
              {metrics?.criticalAlerts || 0}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Chart */}
      <Card>
        <CardHeader>
          <CardTitle>Incidents by Type (Last 30 Days)</CardTitle>
        </CardHeader>
        <CardContent>
          <BarChart data={metrics?.incidentsByType || []} />
        </CardContent>
      </Card>
    </div>
  )
}
```

### 1.3. Custom Components

```typescript
// frontend/src/components/custom/threat-intel-widget.tsx

"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

export function ThreatIntelWidget() {
  const [ioc, setIoc] = useState("")

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["threat-intel", ioc],
    queryFn: async () => {
      if (!ioc) return null
      const response = await fetch(`/api/threat-intel/${ioc}`)
      return response.json()
    },
    enabled: false, // Manual trigger
  })

  const handleLookup = () => {
    refetch()
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          placeholder="Enter IP, domain, or hash..."
          value={ioc}
          onChange={(e) => setIoc(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLookup()}
        />
        <Button onClick={handleLookup} disabled={isLoading}>
          Lookup
        </Button>
      </div>

      {data && (
        <div className="p-4 border rounded-lg space-y-2">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{data.ioc_value}</span>
            <Badge
              variant={
                data.threat_level === "critical"
                  ? "destructive"
                  : data.threat_level === "high"
                    ? "warning"
                    : "default"
              }
            >
              {data.threat_level}
            </Badge>
          </div>

          <div className="text-sm text-muted-foreground">
            <p>Type: {data.ioc_type}</p>
            <p>Source: {data.source}</p>
            <p>
              First seen:{" "}
              {new Date(data.created_at).toLocaleDateString()}
            </p>
          </div>

          {data.metadata && (
            <pre className="text-xs bg-muted p-2 rounded">
              {JSON.stringify(data.metadata, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
```

---

## 2. Docker Packaging

### 2.1. Custom Dockerfile

```dockerfile
# deployment/docker/Dockerfile.custom

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Install pnpm
RUN npm install -g pnpm

# Copy frontend files
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
COPY config/branding.json ./src/config/

# Build Next.js app
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL}

RUN pnpm build

# Stage 2: Build Python backend
FROM python:3.12-slim AS backend-builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies
COPY pyproject.toml uv.lock ./
COPY tracecat/ ./tracecat/
COPY packages/ ./packages/
COPY custom/ ./custom/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Install Python dependencies
RUN uv sync --frozen --no-dev

# Stage 3: Final production image
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python environment from builder
COPY --from=backend-builder /app/.venv /app/.venv
COPY --from=backend-builder /app /app

# Copy frontend build
COPY --from=frontend-builder /app/frontend/.next/standalone /app/frontend/
COPY --from=frontend-builder /app/frontend/.next/static /app/frontend/.next/static
COPY --from=frontend-builder /app/frontend/public /app/frontend/public

# Create non-root user
RUN useradd -m -u 1000 tracecat && \
    chown -R tracecat:tracecat /app

USER tracecat

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# Expose ports
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -f http://localhost:8000/health || exit 1

# Default command (API service)
CMD ["uvicorn", "tracecat.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2.2. Production Docker Compose

```yaml
# deployment/docker/docker-compose.prod.yml

services:
  # Reverse proxy
  caddy:
    image: caddy:2.10.2-alpine
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile.prod:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - tracecat-net

  # API Service
  api:
    build:
      context: ../..
      dockerfile: deployment/docker/Dockerfile.custom
    restart: always
    environment:
      TRACECAT__APP_ENV: production
      TRACECAT__DB_URI: ${TRACECAT__DB_URI}
      TRACECAT__DB_ENCRYPTION_KEY: ${TRACECAT__DB_ENCRYPTION_KEY}
      TRACECAT__SERVICE_KEY: ${TRACECAT__SERVICE_KEY}
      TEMPORAL__CLUSTER_URL: temporal:7233
      REDIS_URL: redis://redis:6379
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - ./custom:/app/custom
    depends_on:
      postgres:
        condition: service_healthy
      temporal:
        condition: service_started
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G

  # Worker Service
  worker:
    build:
      context: ../..
      dockerfile: deployment/docker/Dockerfile.custom
    restart: always
    command: ["python", "-m", "tracecat.dsl.worker"]
    environment:
      TRACECAT__APP_ENV: production
      TRACECAT__DB_URI: ${TRACECAT__DB_URI}
      TRACECAT__DB_ENCRYPTION_KEY: ${TRACECAT__DB_ENCRYPTION_KEY}
      TRACECAT__SERVICE_KEY: ${TRACECAT__SERVICE_KEY}
      TEMPORAL__CLUSTER_URL: temporal:7233
      REDIS_URL: redis://redis:6379
    volumes:
      - ./custom:/app/custom
    depends_on:
      - api
      - temporal
    networks:
      - tracecat-net
    deploy:
      replicas: 3  # Multiple workers
      resources:
        limits:
          cpus: '2'
          memory: 4G

  # Executor Service
  executor:
    build:
      context: ../..
      dockerfile: deployment/docker/Dockerfile.custom
    restart: always
    command:
      [
        "uvicorn",
        "tracecat.api.executor:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
      ]
    environment:
      TRACECAT__APP_ENV: production
      TRACECAT__DB_URI: ${TRACECAT__DB_URI}
      TRACECAT__DB_ENCRYPTION_KEY: ${TRACECAT__DB_ENCRYPTION_KEY}
      TRACECAT__SERVICE_KEY: ${TRACECAT__SERVICE_KEY}
      REDIS_URL: redis://redis:6379
    volumes:
      - ./custom:/app/custom
    depends_on:
      - redis
    networks:
      - tracecat-net
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '4'
          memory: 8G

  # UI Service
  ui:
    build:
      context: ../..
      dockerfile: deployment/docker/Dockerfile.custom
    restart: always
    command: ["node", "frontend/server.js"]
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL}
      NEXT_PUBLIC_APP_URL: ${NEXT_PUBLIC_APP_URL}
      NODE_ENV: production
    depends_on:
      - api
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 2G

  # PostgreSQL (Primary)
  postgres:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgresql.conf:/etc/postgresql/postgresql.conf
    command: postgres -c config_file=/etc/postgresql/postgresql.conf
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G

  # PostgreSQL (Replica - optional)
  postgres-replica:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/replica
    volumes:
      - postgres_replica_data:/var/lib/postgresql/data
    command: >
      bash -c "
        until pg_basebackup --pgdata=/var/lib/postgresql/data/replica -R --slot=replication_slot --host=postgres --port=5432
        do
          echo 'Waiting for primary to be ready...'
          sleep 1
        done
        postgres
      "
    depends_on:
      - postgres
    networks:
      - tracecat-net

  # Temporal
  temporal:
    image: temporalio/auto-setup:1.27.1
    restart: always
    environment:
      DB: postgres12
      DB_PORT: 5432
      POSTGRES_USER: ${TEMPORAL_POSTGRES_USER}
      POSTGRES_PWD: ${TEMPORAL_POSTGRES_PASSWORD}
      POSTGRES_SEEDS: temporal-db
      DYNAMIC_CONFIG_FILE_PATH: config/dynamicconfig/production.yaml
    volumes:
      - ./temporal-config.yaml:/etc/temporal/config/production.yaml
    depends_on:
      - temporal-db
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G

  # Temporal DB
  temporal-db:
    image: postgres:13
    restart: always
    environment:
      POSTGRES_USER: ${TEMPORAL_POSTGRES_USER}
      POSTGRES_PASSWORD: ${TEMPORAL_POSTGRES_PASSWORD}
    volumes:
      - temporal_db_data:/var/lib/postgresql/data
    networks:
      - tracecat-net

  # MinIO (S3-compatible storage)
  minio:
    image: minio/minio:RELEASE.2025-05-24T17-08-30Z
    restart: always
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 2G

  # Redis
  redis:
    image: redis:7-alpine
    restart: always
    command: >
      redis-server
      --appendonly yes
      --maxmemory 2gb
      --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - tracecat-net
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 2G

volumes:
  caddy_data:
  caddy_config:
  postgres_data:
  postgres_replica_data:
  temporal_db_data:
  minio_data:
  redis_data:

networks:
  tracecat-net:
    driver: bridge
```

### 2.3. Caddyfile Production

```caddyfile
# deployment/docker/Caddyfile.prod

{
    # Global options
    email admin@yourcompany.com
    admin off
}

# Main domain
yoursoar.company.com {
    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        X-XSS-Protection "1; mode=block"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
    }

    # API
    handle /api/* {
        reverse_proxy api:8000
    }

    # S3 (MinIO)
    handle /s3/* {
        reverse_proxy minio:9000
    }

    # UI
    handle {
        reverse_proxy ui:3000
    }

    # Logs
    log {
        output file /data/logs/access.log
        format json
    }
}

# MinIO Console (internal only)
minio-console.yoursoar.company.com {
    reverse_proxy minio:9001

    # IP whitelist (internal network only)
    @denied not remote_ip 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
    respond @denied "Access Denied" 403
}

# Temporal UI (internal only)
temporal.yoursoar.company.com {
    reverse_proxy temporal:8233

    # IP whitelist
    @denied not remote_ip 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
    respond @denied "Access Denied" 403
}
```

---

## 3. Kubernetes Deployment

### 3.1. Namespace

```yaml
# deployment/kubernetes/base/namespace.yaml

apiVersion: v1
kind: Namespace
metadata:
  name: tracecat-soar
  labels:
    app: tracecat
    environment: production
```

### 3.2. ConfigMap

```yaml
# deployment/kubernetes/base/configmap.yaml

apiVersion: v1
kind: ConfigMap
metadata:
  name: tracecat-config
  namespace: tracecat-soar
data:
  TRACECAT__APP_ENV: "production"
  TRACECAT__PUBLIC_APP_URL: "https://yoursoar.company.com"
  TRACECAT__PUBLIC_API_URL: "https://yoursoar.company.com/api"
  TEMPORAL__CLUSTER_URL: "temporal:7233"
  TEMPORAL__CLUSTER_QUEUE: "tracecat-task-queue"
  REDIS_URL: "redis://redis:6379"
  LOG_LEVEL: "INFO"
```

### 3.3. Secrets

```yaml
# deployment/kubernetes/base/secrets.yaml

apiVersion: v1
kind: Secret
metadata:
  name: tracecat-secrets
  namespace: tracecat-soar
type: Opaque
stringData:
  TRACECAT__SERVICE_KEY: "your-service-key"
  TRACECAT__DB_ENCRYPTION_KEY: "your-encryption-key"
  TRACECAT__SIGNING_SECRET: "your-signing-secret"
  USER_AUTH_SECRET: "your-auth-secret"
  POSTGRES_PASSWORD: "your-postgres-password"
  MINIO_ROOT_PASSWORD: "your-minio-password"
```

**IMPORTANT**: Đừng commit secrets vào Git. Sử dụng:
- Sealed Secrets
- External Secrets Operator
- HashiCorp Vault
- AWS Secrets Manager
- Azure Key Vault

### 3.4. PostgreSQL StatefulSet

```yaml
# deployment/kubernetes/base/postgresql.yaml

apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: tracecat-soar
spec:
  ports:
    - port: 5432
  clusterIP: None
  selector:
    app: postgres

---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: tracecat-soar
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_USER
              value: "postgres"
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: tracecat-secrets
                  key: POSTGRES_PASSWORD
            - name: POSTGRES_DB
              value: "tracecat"
            - name: PGDATA
              value: "/var/lib/postgresql/data/pgdata"
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              memory: "2Gi"
              cpu: "1"
            limits:
              memory: "4Gi"
              cpu: "2"
  volumeClaimTemplates:
    - metadata:
        name: postgres-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: "fast-ssd"  # Your storage class
        resources:
          requests:
            storage: 100Gi
```

### 3.5. API Deployment

```yaml
# deployment/kubernetes/base/api-deployment.yaml

apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: tracecat-soar
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: your-registry/tracecat-soar:latest
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: tracecat-config
            - secretRef:
                name: tracecat-secrets
          env:
            - name: TRACECAT__DB_URI
              value: "postgresql+psycopg://postgres:$(POSTGRES_PASSWORD)@postgres:5432/tracecat"
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
          resources:
            requests:
              memory: "2Gi"
              cpu: "1"
            limits:
              memory: "4Gi"
              cpu: "2"

---
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: tracecat-soar
spec:
  selector:
    app: api
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
  type: ClusterIP
```

### 3.6. Worker Deployment

```yaml
# deployment/kubernetes/base/worker-deployment.yaml

apiVersion: apps/v1
kind: Deployment
metadata:
  name: worker
  namespace: tracecat-soar
spec:
  replicas: 5  # Scale based on workload
  selector:
    matchLabels:
      app: worker
  template:
    metadata:
      labels:
        app: worker
    spec:
      containers:
        - name: worker
          image: your-registry/tracecat-soar:latest
          command: ["python", "-m", "tracecat.dsl.worker"]
          envFrom:
            - configMapRef:
                name: tracecat-config
            - secretRef:
                name: tracecat-secrets
          env:
            - name: TRACECAT__DB_URI
              value: "postgresql+psycopg://postgres:$(POSTGRES_PASSWORD)@postgres:5432/tracecat"
          resources:
            requests:
              memory: "2Gi"
              cpu: "1"
            limits:
              memory: "4Gi"
              cpu: "2"
```

### 3.7. Ingress

```yaml
# deployment/kubernetes/base/ingress.yaml

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tracecat-ingress
  namespace: tracecat-soar
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - yoursoar.company.com
      secretName: tracecat-tls
  rules:
    - host: yoursoar.company.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ui
                port:
                  number: 3000
```

### 3.8. HorizontalPodAutoscaler

```yaml
# deployment/kubernetes/base/hpa.yaml

apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-hpa
  namespace: tracecat-soar
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80

---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: worker-hpa
  namespace: tracecat-soar
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: worker
  minReplicas: 5
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### 3.9. Deploy Commands

```bash
# Create namespace
kubectl apply -f deployment/kubernetes/base/namespace.yaml

# Create secrets (use sealed-secrets hoặc external-secrets)
kubectl apply -f deployment/kubernetes/base/secrets.yaml

# Create configmap
kubectl apply -f deployment/kubernetes/base/configmap.yaml

# Deploy PostgreSQL
kubectl apply -f deployment/kubernetes/base/postgresql.yaml

# Wait for PostgreSQL
kubectl wait --for=condition=ready pod -l app=postgres -n tracecat-soar --timeout=300s

# Run migrations
kubectl run -it --rm migration \
  --image=your-registry/tracecat-soar:latest \
  --env="TRACECAT__DB_URI=postgresql+psycopg://postgres:password@postgres:5432/tracecat" \
  --restart=Never \
  --namespace=tracecat-soar \
  -- alembic upgrade head

# Deploy services
kubectl apply -f deployment/kubernetes/base/

# Check status
kubectl get pods -n tracecat-soar
kubectl get svc -n tracecat-soar
kubectl get ingress -n tracecat-soar
```

---

## 4. Production Configuration

### 4.1. PostgreSQL Tuning

```conf
# deployment/docker/postgresql.conf

# Connections
max_connections = 200
shared_buffers = 4GB
effective_cache_size = 12GB
maintenance_work_mem = 1GB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 10485kB
min_wal_size = 2GB
max_wal_size = 8GB
max_worker_processes = 4
max_parallel_workers_per_gather = 2
max_parallel_workers = 4
max_parallel_maintenance_workers = 2
```

### 4.2. Redis Configuration

```conf
# deployment/docker/redis.conf

# Memory
maxmemory 2gb
maxmemory-policy allkeys-lru

# Persistence
appendonly yes
appendfsync everysec

# Performance
tcp-backlog 511
timeout 0
tcp-keepalive 300
```

### 4.3. Environment Variables

```bash
# .env.production

# ==================
# PRODUCTION CONFIG
# ==================

TRACECAT__APP_ENV=production

# URLs
TRACECAT__PUBLIC_APP_URL=https://yoursoar.company.com
TRACECAT__PUBLIC_API_URL=https://yoursoar.company.com/api

# Security (use strong random values)
TRACECAT__SERVICE_KEY=<generate-with-secrets.token_urlsafe(32)>
TRACECAT__SIGNING_SECRET=<generate-with-secrets.token_urlsafe(32)>
USER_AUTH_SECRET=<generate-with-secrets.token_urlsafe(32)>
TRACECAT__DB_ENCRYPTION_KEY=<generate-with-Fernet.generate_key()>

# Database (use managed RDS/CloudSQL in production)
TRACECAT__DB_URI=postgresql+psycopg://user:pass@postgres-host:5432/tracecat
TRACECAT__DB_POOL_SIZE=20
TRACECAT__DB_MAX_OVERFLOW=100
TRACECAT__DB_SSLMODE=require

# CORS
TRACECAT__ALLOW_ORIGINS=https://yoursoar.company.com

# Logging
LOG_LEVEL=INFO

# Monitoring
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project

# Features
TRACECAT__FEATURE_FLAGS=agent_presets,case_durations,git_sync
```

---

(Còn tiếp phần 5-8...)
