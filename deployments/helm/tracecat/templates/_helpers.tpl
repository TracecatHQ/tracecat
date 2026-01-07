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
{{- else }}
{{- printf "https://%s/s3" .Values.ingress.host }}
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
{{- else if .Values.minio.enabled }}
{{- printf "http://%s:9000" .Values.minio.fullnameOverride }}
{{- else }}
{{- fail "tracecat.blobStorage.endpoint is required when minio is disabled" }}
{{- end }}
{{- end }}

{{/*
Temporal Cluster URL - supports both self-hosted and Temporal Cloud
*/}}
{{- define "tracecat.temporalClusterUrl" -}}
{{- if .Values.temporalCloud.clusterUrl }}
{{- .Values.temporalCloud.clusterUrl }}
{{- else if .Values.tracecat.temporal.clusterUrl }}
{{- .Values.tracecat.temporal.clusterUrl }}
{{- else if .Values.temporal.enabled }}
{{- printf "%s-temporal-frontend:7233" .Release.Name }}
{{- else }}
{{- fail "tracecat.temporal.clusterUrl or temporalCloud.clusterUrl is required when temporal subchart is disabled" }}
{{- end }}
{{- end }}

{{/*
Temporal Namespace
*/}}
{{- define "tracecat.temporalNamespace" -}}
{{- if .Values.temporalCloud.namespace }}
{{- .Values.temporalCloud.namespace }}
{{- else if .Values.tracecat.temporal.clusterNamespace }}
{{- .Values.tracecat.temporal.clusterNamespace }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{/*
Temporal Queue
*/}}
{{- define "tracecat.temporalQueue" -}}
{{- if .Values.temporalCloud.clusterQueue }}
{{- .Values.temporalCloud.clusterQueue }}
{{- else if .Values.tracecat.temporal.clusterQueue }}
{{- .Values.tracecat.temporal.clusterQueue }}
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
  value: {{ .Values.tracecat.featureFlags | quote }}
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
{{- end }}

{{/*
Blob storage environment variables
*/}}
{{- define "tracecat.env.blobStorage" -}}
- name: TRACECAT__BLOB_STORAGE_ENDPOINT
  value: {{ include "tracecat.blobStorageEndpoint" . | quote }}
{{- end }}

{{/*
PostgreSQL environment variables
Constructs TRACECAT__DB_URI from computed host/port/database/sslmode and secret credentials
*/}}
{{- define "tracecat.env.postgres" -}}
{{- $secretName := include "tracecat.postgres.secretName" . }}
{{- $host := include "tracecat.postgres.host" . }}
{{- $port := include "tracecat.postgres.port" . }}
{{- $database := include "tracecat.postgres.database" . }}
{{- $sslMode := include "tracecat.postgres.sslMode" . }}
{{- if $secretName }}
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
{{- if .Values.externalRedis.auth.existingSecret }}
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalRedis.auth.existingSecret }}
      key: url
{{- else }}
{{- fail "externalRedis.auth.existingSecret is required when using external Redis" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
API service environment variables
Merges: common + temporal + postgres + redis + api-specific
*/}}
{{- define "tracecat.env.api" -}}
{{- include "tracecat.env.common" . }}
{{- include "tracecat.env.temporal" . }}
{{- include "tracecat.env.blobStorage" . }}
{{- include "tracecat.env.postgres" . }}
{{- include "tracecat.env.redis" . }}
- name: TRACECAT__API_ROOT_PATH
  value: {{ .Values.api.apiRootPath | quote }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
- name: TRACECAT__PUBLIC_APP_URL
  value: {{ include "tracecat.publicAppUrl" . | quote }}
- name: TRACECAT__PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
- name: TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT
  value: {{ include "tracecat.publicS3Url" . | quote }}
- name: TRACECAT__ALLOW_ORIGINS
  value: {{ .Values.api.allowOrigins | quote }}
- name: RUN_MIGRATIONS
  value: {{ .Values.api.runMigrations | quote }}
- name: TEMPORAL__TASK_TIMEOUT
  value: {{ .Values.tracecat.temporal.taskTimeout | quote }}
{{- /* Auth settings */}}
- name: TRACECAT__AUTH_TYPES
  value: {{ .Values.api.auth.types | quote }}
- name: TRACECAT__AUTH_ALLOWED_DOMAINS
  value: {{ .Values.api.auth.allowedDomains | quote }}
- name: TRACECAT__AUTH_MIN_PASSWORD_LENGTH
  value: {{ .Values.api.auth.minPasswordLength | quote }}
- name: TRACECAT__AUTH_SUPERADMIN_EMAIL
  value: {{ .Values.api.auth.superadminEmail | quote }}
{{- /* SAML settings */}}
{{- if .Values.api.saml.enabled }}
- name: SAML_IDP_METADATA_URL
  value: {{ .Values.api.saml.idpMetadataUrl | quote }}
- name: SAML_ALLOW_UNSOLICITED
  value: {{ .Values.api.saml.allowUnsolicited | quote }}
- name: SAML_ACCEPTED_TIME_DIFF
  value: {{ .Values.api.saml.acceptedTimeDiff | quote }}
- name: SAML_AUTHN_REQUESTS_SIGNED
  value: {{ .Values.api.saml.authnRequestsSigned | quote }}
- name: SAML_SIGNED_ASSERTIONS
  value: {{ .Values.api.saml.signedAssertions | quote }}
- name: SAML_SIGNED_RESPONSES
  value: {{ .Values.api.saml.signedResponses | quote }}
- name: SAML_VERIFY_SSL_ENTITY
  value: {{ .Values.api.saml.verifySslEntity | quote }}
- name: SAML_VERIFY_SSL_METADATA
  value: {{ .Values.api.saml.verifySslMetadata | quote }}
- name: SAML_CA_CERTS
  value: {{ .Values.api.saml.caCerts | quote }}
- name: SAML_METADATA_CERT
  value: {{ .Values.api.saml.metadataCert | quote }}
{{- end }}
{{- /* Local repository settings */}}
- name: TRACECAT__LOCAL_REPOSITORY_ENABLED
  value: {{ .Values.api.localRepository.enabled | quote }}
- name: TRACECAT__LOCAL_REPOSITORY_PATH
  value: {{ .Values.api.localRepository.path | quote }}
{{- /* Streaming */}}
- name: TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED
  value: {{ .Values.api.unifiedAgentStreamingEnabled | quote }}
{{- end }}

{{/*
Worker service environment variables
Merges: common + temporal + postgres + redis + worker-specific
*/}}
{{- define "tracecat.env.worker" -}}
{{- include "tracecat.env.common" . }}
{{- include "tracecat.env.temporal" . }}
{{- include "tracecat.env.postgres" . }}
{{- include "tracecat.env.redis" . }}
- name: TRACECAT__API_ROOT_PATH
  value: {{ .Values.api.apiRootPath | quote }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
- name: TRACECAT__PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.worker.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.worker.contextCompression.thresholdKb | quote }}
{{- /* Local repository settings */}}
- name: TRACECAT__LOCAL_REPOSITORY_ENABLED
  value: {{ .Values.worker.localRepository.enabled | quote }}
- name: TRACECAT__LOCAL_REPOSITORY_PATH
  value: {{ .Values.worker.localRepository.path | quote }}
{{- /* Sentry */}}
{{- if .Values.worker.sentryDsn }}
- name: SENTRY_DSN
  value: {{ .Values.worker.sentryDsn | quote }}
{{- end }}
{{- end }}

{{/*
Executor service environment variables
Merges: common + temporal + postgres + redis + executor-specific
*/}}
{{- define "tracecat.env.executor" -}}
{{- include "tracecat.env.common" . }}
{{- include "tracecat.env.temporal" . }}
{{- include "tracecat.env.blobStorage" . }}
{{- include "tracecat.env.postgres" . }}
{{- include "tracecat.env.redis" . }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.executor.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.executor.contextCompression.thresholdKb | quote }}
{{- /* Sandbox settings */}}
- name: TRACECAT__DISABLE_NSJAIL
  value: {{ .Values.executor.sandbox.disableNsjail | quote }}
- name: TRACECAT__SANDBOX_NSJAIL_PATH
  value: {{ .Values.executor.sandbox.nsjailPath | quote }}
- name: TRACECAT__SANDBOX_ROOTFS_PATH
  value: {{ .Values.executor.sandbox.rootfsPath | quote }}
- name: TRACECAT__SANDBOX_CACHE_DIR
  value: {{ .Values.executor.sandbox.cacheDir | quote }}
{{- /* Executor settings */}}
- name: TRACECAT__EXECUTOR_QUEUE
  value: {{ .Values.executor.queue | quote }}
- name: TRACECAT__EXECUTOR_WORKER_POOL_SIZE
  value: {{ .Values.executor.workerPoolSize | quote }}
{{- /* Secret masking */}}
- name: TRACECAT__UNSAFE_DISABLE_SM_MASKING
  value: {{ .Values.executor.unsafeDisableSmMasking | quote }}
{{- /* Local repository settings */}}
- name: TRACECAT__LOCAL_REPOSITORY_ENABLED
  value: {{ .Values.executor.localRepository.enabled | quote }}
- name: TRACECAT__LOCAL_REPOSITORY_PATH
  value: {{ .Values.executor.localRepository.path | quote }}
{{- end }}

{{/*
UI service environment variables
*/}}
{{- define "tracecat.env.ui" -}}
- name: NODE_ENV
  value: {{ .Values.ui.nodeEnv | quote }}
- name: NEXT_PUBLIC_APP_ENV
  value: {{ .Values.tracecat.appEnv | quote }}
- name: NEXT_PUBLIC_APP_URL
  value: {{ include "tracecat.publicAppUrl" . | quote }}
- name: NEXT_PUBLIC_API_URL
  value: {{ include "tracecat.publicApiUrl" . | quote }}
- name: NEXT_PUBLIC_AUTH_TYPES
  value: {{ .Values.ui.authTypes | quote }}
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
