{{/*
Tracecat Helm Chart Helpers
Following patterns from terraform-fargate/modules/ecs/locals.tf
*/}}

{{/*
Chart name
*/}}
{{- define "tracecat.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name (truncated to 63 chars for K8s naming)
*/}}
{{- define "tracecat.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version for chart label
*/}}
{{- define "tracecat.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "tracecat.labels" -}}
helm.sh/chart: {{ include "tracecat.chart" . }}
{{ include "tracecat.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "tracecat.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tracecat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
=============================================================================
PostgreSQL Helpers
=============================================================================
*/}}

{{/*
PostgreSQL Host - CloudNativePG uses <name>-rw service for read-write access
*/}}
{{- define "tracecat.postgres.host" -}}
{{- if .Values.postgres.enabled }}
{{- printf "%s-rw" .Values.postgres.fullnameOverride }}
{{- else if .Values.externalPostgres.enabled }}
{{- .Values.externalPostgres.host }}
{{- else }}
{{- fail "Either postgres.enabled or externalPostgres.enabled must be true" }}
{{- end }}
{{- end }}

{{/*
PostgreSQL Port
*/}}
{{- define "tracecat.postgres.port" -}}
{{- if .Values.postgres.enabled }}
{{- "5432" }}
{{- else if .Values.externalPostgres.enabled }}
{{- .Values.externalPostgres.port | default "5432" }}
{{- else }}
{{- "5432" }}
{{- end }}
{{- end }}

{{/*
PostgreSQL Database Name
*/}}
{{- define "tracecat.postgres.database" -}}
{{- if .Values.postgres.enabled }}
{{- "app" }}{{/* CloudNativePG cluster chart creates 'app' database by default */}}
{{- else if .Values.externalPostgres.enabled }}
{{- .Values.externalPostgres.database | default "tracecat" }}
{{- else }}
{{- "tracecat" }}
{{- end }}
{{- end }}

{{/*
PostgreSQL SSL Mode
*/}}
{{- define "tracecat.postgres.sslMode" -}}
{{- if .Values.postgres.enabled }}
{{- "prefer" }}
{{- else if .Values.externalPostgres.enabled }}
{{- .Values.externalPostgres.sslMode | default "prefer" }}
{{- else }}
{{- "prefer" }}
{{- end }}
{{- end }}

{{/*
PostgreSQL Secret Name - the secret containing username and password
For CloudNativePG, this is auto-generated as <cluster-name>-app
*/}}
{{- define "tracecat.postgres.secretName" -}}
{{- if .Values.postgres.enabled }}
{{- printf "%s-app" .Values.postgres.fullnameOverride }}
{{- else if .Values.externalPostgres.enabled }}
{{- .Values.externalPostgres.auth.existingSecret }}
{{- else }}
{{- "" }}
{{- end }}
{{- end }}

{{/*
=============================================================================
Redis/Valkey Helpers
=============================================================================
*/}}

{{/*
Redis Host
*/}}
{{- define "tracecat.redis.host" -}}
{{- if .Values.redis.enabled }}
{{- .Values.redis.fullnameOverride }}
{{- else if .Values.externalRedis.enabled }}
{{- "external" }}{{/* External Redis uses URL from secret */}}
{{- else }}
{{- fail "Either redis.enabled or externalRedis.enabled must be true" }}
{{- end }}
{{- end }}

{{/*
Redis Port
*/}}
{{- define "tracecat.redis.port" -}}
{{- "6379" }}
{{- end }}

{{/*
Redis Secret Name - for external Redis containing the URL
*/}}
{{- define "tracecat.redis.secretName" -}}
{{- if .Values.redis.enabled }}
{{- "" }}{{/* Internal Valkey doesn't need a secret for URL */}}
{{- else if .Values.externalRedis.enabled }}
{{- .Values.externalRedis.auth.existingSecret }}
{{- else }}
{{- "" }}
{{- end }}
{{- end }}

{{/*
=============================================================================
URL Helpers
=============================================================================
*/}}

{{/*
Public App URL - used for browser redirects and public-facing links
*/}}
{{- define "tracecat.publicAppUrl" -}}
{{- if .Values.urls.publicApp }}
{{- .Values.urls.publicApp }}
{{- else }}
{{- printf "https://%s" .Values.ingress.host }}
{{- end }}
{{- end }}

{{/*
Public API URL - used for external API access
*/}}
{{- define "tracecat.publicApiUrl" -}}
{{- if .Values.urls.publicApi }}
{{- .Values.urls.publicApi }}
{{- else }}
{{- printf "https://%s/api" .Values.ingress.host }}
{{- end }}
{{- end }}

{{/*
Public S3 URL - used for presigned URLs
*/}}
{{- define "tracecat.publicS3Url" -}}
{{- if .Values.urls.publicS3 }}
{{- .Values.urls.publicS3 }}
{{- else if .Values.minio.enabled }}
{{- printf "https://%s/s3" .Values.ingress.host }}
{{- else }}
{{- "" }}
{{- end }}
{{- end }}

{{/*
Internal API URL - used for service-to-service communication
*/}}
{{- define "tracecat.internalApiUrl" -}}
{{- printf "http://%s-api:8000" (include "tracecat.fullname" .) }}
{{- end }}

{{/*
Internal Blob Storage URL
*/}}
{{- define "tracecat.blobStorageEndpoint" -}}
{{- if .Values.tracecat.blobStorage.endpoint }}
{{- .Values.tracecat.blobStorage.endpoint }}
{{- else if .Values.externalS3.enabled }}
{{- .Values.externalS3.endpoint | default "" }}
{{- else if .Values.minio.enabled }}
{{- printf "http://%s:9000" .Values.minio.fullnameOverride }}
{{- else }}
{{- fail "tracecat.blobStorage.endpoint or externalS3.enabled is required when minio is disabled" }}
{{- end }}
{{- end }}

{{/*
Temporal Cluster URL - supports both subchart and external Temporal
*/}}
{{- define "tracecat.temporalClusterUrl" -}}
{{- if .Values.temporal.enabled }}
{{- printf "%s-temporal-frontend:7233" .Release.Name }}
{{- else if .Values.externalTemporal.enabled }}
{{- required "externalTemporal.clusterUrl is required when using external Temporal" .Values.externalTemporal.clusterUrl }}
{{- else }}
{{- fail "Either temporal.enabled or externalTemporal.enabled must be true" }}
{{- end }}
{{- end }}

{{/*
Temporal Namespace
*/}}
{{- define "tracecat.temporalNamespace" -}}
{{- if .Values.temporal.enabled }}
{{- "default" }}
{{- else if .Values.externalTemporal.enabled }}
{{- .Values.externalTemporal.clusterNamespace | default "default" }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{/*
Temporal Queue
*/}}
{{- define "tracecat.temporalQueue" -}}
{{- if .Values.temporal.enabled }}
{{- "tracecat-task-queue" }}
{{- else if .Values.externalTemporal.enabled }}
{{- .Values.externalTemporal.clusterQueue | default "tracecat-task-queue" }}
{{- else }}
{{- "tracecat-task-queue" }}
{{- end }}
{{- end }}

{{/*
=============================================================================
Environment Variable Helpers
Following the pattern from terraform-fargate locals.tf where env vars are
computed centrally and merged per-service.
=============================================================================
*/}}

{{/*
Common environment variables shared across all backend services
(api, worker, executor)
*/}}
{{- define "tracecat.env.common" -}}
- name: LOG_LEVEL
  value: {{ .Values.tracecat.logLevel | quote }}
- name: TRACECAT__APP_ENV
  value: {{ .Values.tracecat.appEnv | quote }}
- name: TRACECAT__FEATURE_FLAGS
  value: {{ .Values.enterprise.featureFlags | quote }}
{{- end }}

{{/*
Temporal environment variables (shared by api, worker, executor)
*/}}
{{- define "tracecat.env.temporal" -}}
- name: TEMPORAL__CLUSTER_URL
  value: {{ include "tracecat.temporalClusterUrl" . | quote }}
- name: TEMPORAL__CLUSTER_NAMESPACE
  value: {{ include "tracecat.temporalNamespace" . | quote }}
- name: TEMPORAL__CLUSTER_QUEUE
  value: {{ include "tracecat.temporalQueue" . | quote }}
{{- if .Values.externalTemporal.enabled }}
{{- if .Values.externalTemporal.auth.secretArn }}
- name: TEMPORAL__API_KEY__ARN
  value: {{ .Values.externalTemporal.auth.secretArn | quote }}
{{- else if .Values.externalTemporal.auth.existingSecret }}
- name: TEMPORAL__API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalTemporal.auth.existingSecret }}
      key: apiKey
{{- end }}
{{- end }}
{{- end }}

{{/*
Blob storage environment variables
*/}}
{{- define "tracecat.env.blobStorage" -}}
{{- $endpoint := include "tracecat.blobStorageEndpoint" . }}
{{- if $endpoint }}
- name: TRACECAT__BLOB_STORAGE_ENDPOINT
  value: {{ $endpoint | quote }}
{{- end }}
{{- if .Values.tracecat.blobStorage.buckets.attachments }}
- name: TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS
  value: {{ .Values.tracecat.blobStorage.buckets.attachments | quote }}
{{- end }}
{{- if .Values.tracecat.blobStorage.buckets.registry }}
- name: TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
  value: {{ .Values.tracecat.blobStorage.buckets.registry | quote }}
{{- end }}
{{- if .Values.externalS3.enabled }}
{{- if .Values.externalS3.region }}
- name: AWS_REGION
  value: {{ .Values.externalS3.region | quote }}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.externalS3.region | quote }}
{{- end }}
{{- if .Values.externalS3.auth.existingSecret }}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalS3.auth.existingSecret }}
      key: accessKeyId
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalS3.auth.existingSecret }}
      key: secretAccessKey
{{- end }}
{{- end }}
{{- end }}

{{/*
PostgreSQL environment variables
Constructs TRACECAT__DB_URI from computed host/port/database/sslmode and secret credentials
*/}}
{{- define "tracecat.env.postgres" -}}
{{- $host := include "tracecat.postgres.host" . }}
{{- $port := include "tracecat.postgres.port" . }}
{{- $database := include "tracecat.postgres.database" . }}
{{- $sslMode := include "tracecat.postgres.sslMode" . }}
{{- if .Values.postgres.enabled }}
{{- $secretName := include "tracecat.postgres.secretName" . }}
- name: TRACECAT__POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: username
- name: TRACECAT__POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: password
- name: TRACECAT__DB_URI
  value: "postgresql+psycopg://$(TRACECAT__POSTGRES_USER):$(TRACECAT__POSTGRES_PASSWORD)@{{ $host }}:{{ $port }}/{{ $database }}?sslmode={{ $sslMode }}"
{{- else if .Values.externalPostgres.enabled }}
{{- if .Values.externalPostgres.auth.secretArn }}
{{- if .Values.externalPostgres.auth.username }}
- name: TRACECAT__DB_USER
  value: {{ .Values.externalPostgres.auth.username | quote }}
{{- end }}
- name: TRACECAT__DB_PASS__ARN
  value: {{ .Values.externalPostgres.auth.secretArn | quote }}
- name: TRACECAT__DB_ENDPOINT
  value: {{ $host | quote }}
- name: TRACECAT__DB_PORT
  value: {{ $port | quote }}
- name: TRACECAT__DB_NAME
  value: {{ $database | quote }}
- name: TRACECAT__DB_SSLMODE
  value: {{ $sslMode | quote }}
{{- else if .Values.externalPostgres.auth.existingSecret }}
- name: TRACECAT__DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalPostgres.auth.existingSecret }}
      key: username
- name: TRACECAT__DB_PASS
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalPostgres.auth.existingSecret }}
      key: password
- name: TRACECAT__DB_ENDPOINT
  value: {{ $host | quote }}
- name: TRACECAT__DB_PORT
  value: {{ $port | quote }}
- name: TRACECAT__DB_NAME
  value: {{ $database | quote }}
- name: TRACECAT__DB_SSLMODE
  value: {{ $sslMode | quote }}
{{- else }}
{{- fail "externalPostgres.auth.existingSecret or externalPostgres.auth.secretArn is required when using external Postgres" }}
{{- end }}
{{- else }}
{{- fail "PostgreSQL secret name is required" }}
{{- end }}
{{- end }}

{{/*
Redis environment variables
Constructs REDIS_URL from computed host/port or from external secret
*/}}
{{- define "tracecat.env.redis" -}}
{{- if .Values.redis.enabled }}
{{- $host := include "tracecat.redis.host" . }}
{{- $port := include "tracecat.redis.port" . }}
- name: REDIS_URL
  value: "redis://{{ $host }}:{{ $port }}"
{{- else if .Values.externalRedis.enabled }}
{{- if .Values.externalRedis.auth.secretArn }}
- name: REDIS_URL__ARN
  value: {{ .Values.externalRedis.auth.secretArn | quote }}
{{- else if .Values.externalRedis.auth.existingSecret }}
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalRedis.auth.existingSecret }}
      key: url
{{- else }}
{{- fail "externalRedis.auth.existingSecret or externalRedis.auth.secretArn is required when using external Redis" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
API service environment variables
Merges: common + temporal + postgres + redis + api-specific
*/}}
{{- define "tracecat.env.api" -}}
{{ include "tracecat.env.common" . }}
{{ include "tracecat.env.temporal" . }}
{{ include "tracecat.env.blobStorage" . }}
{{ include "tracecat.env.postgres" . }}
{{ include "tracecat.env.redis" . }}
- name: TRACECAT__API_ROOT_PATH
  value: "/api"
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
- name: TRACECAT__PUBLIC_APP_URL
  value: {{ include "tracecat.publicAppUrl" . | quote }}
- name: TRACECAT__PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
{{- $publicS3Url := include "tracecat.publicS3Url" . }}
{{- if $publicS3Url }}
- name: TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT
  value: {{ $publicS3Url | quote }}
{{- end }}
- name: TRACECAT__ALLOW_ORIGINS
  value: {{ .Values.tracecat.allowOrigins | quote }}
- name: RUN_MIGRATIONS
  value: "true"
{{- /* Auth settings */}}
- name: TRACECAT__AUTH_TYPES
  value: {{ .Values.tracecat.auth.types | quote }}
- name: TRACECAT__AUTH_ALLOWED_DOMAINS
  value: {{ .Values.tracecat.auth.allowedDomains | quote }}
- name: TRACECAT__AUTH_MIN_PASSWORD_LENGTH
  value: "16"
- name: TRACECAT__AUTH_SUPERADMIN_EMAIL
  value: {{ .Values.tracecat.auth.superadminEmail | quote }}
{{- /* SAML settings */}}
{{- if .Values.tracecat.saml.enabled }}
- name: SAML_IDP_METADATA_URL
  value: {{ .Values.tracecat.saml.idpMetadataUrl | quote }}
- name: SAML_ALLOW_UNSOLICITED
  value: {{ .Values.tracecat.saml.allowUnsolicited | quote }}
- name: SAML_ACCEPTED_TIME_DIFF
  value: {{ .Values.tracecat.saml.acceptedTimeDiff | quote }}
- name: SAML_AUTHN_REQUESTS_SIGNED
  value: {{ .Values.tracecat.saml.authnRequestsSigned | quote }}
- name: SAML_SIGNED_ASSERTIONS
  value: {{ .Values.tracecat.saml.signedAssertions | quote }}
- name: SAML_SIGNED_RESPONSES
  value: {{ .Values.tracecat.saml.signedResponses | quote }}
- name: SAML_VERIFY_SSL_ENTITY
  value: {{ .Values.tracecat.saml.verifySslEntity | quote }}
- name: SAML_VERIFY_SSL_METADATA
  value: {{ .Values.tracecat.saml.verifySslMetadata | quote }}
- name: SAML_CA_CERTS
  value: {{ .Values.tracecat.saml.caCerts | quote }}
- name: SAML_METADATA_CERT
  value: {{ .Values.tracecat.saml.metadataCert | quote }}
{{- end }}
{{- /* Streaming */}}
- name: TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED
  value: "true"
{{- end }}

{{/*
Worker service environment variables
Merges: common + temporal + postgres + redis + worker-specific
*/}}
{{- define "tracecat.env.worker" -}}
{{ include "tracecat.env.common" . }}
{{ include "tracecat.env.temporal" . }}
{{ include "tracecat.env.postgres" . }}
{{ include "tracecat.env.redis" . }}
- name: TRACECAT__API_ROOT_PATH
  value: "/api"
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
- name: TRACECAT__PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.worker.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.worker.contextCompression.thresholdKb | quote }}
{{- /* Sentry */}}
{{- if .Values.tracecat.sentryDsn }}
- name: SENTRY_DSN
  value: {{ .Values.tracecat.sentryDsn | quote }}
{{- end }}
{{- end }}

{{/*
Executor service environment variables
Merges: common + temporal + postgres + redis + executor-specific
*/}}
{{- define "tracecat.env.executor" -}}
{{ include "tracecat.env.common" . }}
{{ include "tracecat.env.temporal" . }}
{{ include "tracecat.env.blobStorage" . }}
{{ include "tracecat.env.postgres" . }}
{{ include "tracecat.env.redis" . }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.executor.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.executor.contextCompression.thresholdKb | quote }}
{{- /* Sandbox settings */}}
- name: TRACECAT__DISABLE_NSJAIL
  value: {{ .Values.tracecat.sandbox.disableNsjail | quote }}
- name: TRACECAT__SANDBOX_NSJAIL_PATH
  value: "/usr/local/bin/nsjail"
- name: TRACECAT__SANDBOX_ROOTFS_PATH
  value: "/var/lib/tracecat/sandbox-rootfs"
- name: TRACECAT__SANDBOX_CACHE_DIR
  value: "/var/lib/tracecat/sandbox-cache"
{{- /* Executor settings */}}
- name: TRACECAT__EXECUTOR_QUEUE
  value: {{ .Values.executor.queue | quote }}
- name: TRACECAT__EXECUTOR_WORKER_POOL_SIZE
  value: {{ .Values.executor.workerPoolSize | quote }}
{{- /* Secret masking */}}
- name: TRACECAT__UNSAFE_DISABLE_SM_MASKING
  value: "false"
{{- end }}

{{/*
UI service environment variables
*/}}
{{- define "tracecat.env.ui" -}}
- name: NODE_ENV
  value: "production"
- name: NEXT_PUBLIC_APP_ENV
  value: {{ .Values.tracecat.appEnv | quote }}
- name: NEXT_PUBLIC_APP_URL
  value: {{ include "tracecat.publicAppUrl" . | quote }}
- name: NEXT_PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
- name: NEXT_PUBLIC_AUTH_TYPES
  value: {{ .Values.tracecat.auth.types | quote }}
- name: NEXT_SERVER_API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
{{- end }}

{{/*
=============================================================================
Secret Reference Helpers
=============================================================================
*/}}

{{/*
Helper to determine if a secret reference is an AWS Secrets Manager ARN
*/}}
{{- define "tracecat.isAwsSecret" -}}
{{- if and . (hasPrefix "arn:aws" .) }}true{{- end }}
{{- end }}

{{/*
Generate secret key reference for Kubernetes secrets
Usage: {{ include "tracecat.secretKeyRef" (dict "secretName" "my-secret" "key" "password") }}
*/}}
{{- define "tracecat.secretKeyRef" -}}
valueFrom:
  secretKeyRef:
    name: {{ .secretName }}
    key: {{ .key }}
{{- end }}

{{/*
=============================================================================
Validation Helpers
=============================================================================
*/}}

{{/*
Validate required secrets
*/}}
{{- define "tracecat.validateRequiredSecrets" -}}
{{- if not .Values.secrets.existingSecret -}}
{{- fail "secrets.existingSecret is required (K8s Secret with core credentials)" -}}
{{- end -}}
{{- end -}}

{{/*
Validate auth config on first install
*/}}
{{- define "tracecat.validateAuthConfig" -}}
{{- if .Release.IsInstall -}}
{{- if not .Values.tracecat.auth.superadminEmail -}}
{{- fail "tracecat.auth.superadminEmail is required on first install" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Validate infrastructure dependencies
*/}}
{{- define "tracecat.validateInfrastructure" -}}
{{- if and (not .Values.postgres.enabled) (not .Values.externalPostgres.enabled) -}}
{{- fail "Either postgres.enabled or externalPostgres.enabled must be true" -}}
{{- end -}}
{{- if and (not .Values.redis.enabled) (not .Values.externalRedis.enabled) -}}
{{- fail "Either redis.enabled or externalRedis.enabled must be true" -}}
{{- end -}}
{{- if and (not .Values.temporal.enabled) (not .Values.externalTemporal.enabled) -}}
{{- fail "Either temporal.enabled or externalTemporal.enabled must be true" -}}
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Secret Environment Variable Helpers
=============================================================================
*/}}

{{/*
Secret environment variables (shared by api, worker, executor)
*/}}
{{- define "tracecat.env.secrets" -}}
- name: TRACECAT__DB_ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: dbEncryptionKey
- name: TRACECAT__SERVICE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: serviceKey
- name: TRACECAT__SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: signingSecret
{{- end -}}

{{/*
API-specific secret env vars (OAuth, user auth)
*/}}
{{- define "tracecat.env.secrets.api" -}}
{{- include "tracecat.env.secrets" . }}
- name: USER_AUTH_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: userAuthSecret
{{- if .Values.secrets.oauthSecret }}
- name: OAUTH_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.oauthSecret }}
      key: oauthClientId
      optional: true
- name: OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.oauthSecret }}
      key: oauthClientSecret
      optional: true
{{- end }}
{{- end -}}

{{/*
UI-specific secret env vars
*/}}
{{- define "tracecat.env.secrets.ui" -}}
- name: TRACECAT__SERVICE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: serviceKey
{{- end -}}
