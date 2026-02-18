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
Shared backend image tag used by backend workloads and version labels
*/}}
{{- define "tracecat.imageTag" -}}
{{- .Values.image.tag | default .Chart.AppVersion -}}
{{- end }}

{{/*
UI image tag used by frontend workload
*/}}
{{- define "tracecat.uiImageTag" -}}
{{- .Values.uiImage.tag | default .Chart.AppVersion -}}
{{- end }}

{{/*
Common labels
*/}}
{{- define "tracecat.labels" -}}
helm.sh/chart: {{ include "tracecat.chart" . }}
{{ include "tracecat.selectorLabels" . }}
{{- $version := include "tracecat.imageTag" . | trim }}
{{- if $version }}
app.kubernetes.io/version: {{ $version | quote }}
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
Service account name for Tracecat workloads
*/}}
{{- define "tracecat.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- if .Values.serviceAccount.name -}}
{{- .Values.serviceAccount.name -}}
{{- else -}}
{{- printf "%s-app" (include "tracecat.fullname" .) -}}
{{- end -}}
{{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end }}

{{/*
Pod scheduling helpers (nodeSelector, affinity, tolerations, topology spread)
*/}}
{{- define "tracecat.topologySpreadConstraints" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $constraints := .constraints -}}
{{- if $constraints }}
topologySpreadConstraints:
{{- range $c := $constraints }}
  - maxSkew: {{ $c.maxSkew | default 1 }}
    topologyKey: {{ $c.topologyKey | quote }}
    whenUnsatisfiable: {{ $c.whenUnsatisfiable | default "ScheduleAnyway" }}
    {{- if $c.minDomains }}
    minDomains: {{ $c.minDomains }}
    {{- end }}
    labelSelector:
      matchLabels:
        {{- include "tracecat.selectorLabels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ $component }}
{{- end }}
{{- end }}
{{- end }}

{{- define "tracecat.podScheduling" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $scheduling := $root.Values.scheduling -}}
{{- if $scheduling }}
{{- with $scheduling.nodeSelector }}
nodeSelector:
{{ toYaml . | nindent 2 }}
{{- end }}
{{- with $scheduling.affinity }}
affinity:
{{ toYaml . | nindent 2 }}
{{- end }}
{{- with $scheduling.tolerations }}
tolerations:
{{ toYaml . | nindent 2 }}
{{- end }}
{{- if $scheduling.topologySpreadConstraints }}
{{ include "tracecat.topologySpreadConstraints" (dict "root" $root "component" $component "constraints" $scheduling.topologySpreadConstraints) }}
{{- end }}
{{- end }}
{{- end }}

{{/*
=============================================================================
PostgreSQL Helpers
=============================================================================
*/}}

{{/*
PostgreSQL Host
*/}}
{{- define "tracecat.postgres.host" -}}
{{- required "externalPostgres.host is required" .Values.externalPostgres.host }}
{{- end }}

{{/*
PostgreSQL Port
*/}}
{{- define "tracecat.postgres.port" -}}
{{- .Values.externalPostgres.port | default "5432" }}
{{- end }}

{{/*
PostgreSQL Database Name
*/}}
{{- define "tracecat.postgres.database" -}}
{{- .Values.externalPostgres.database | default "tracecat" }}
{{- end }}

{{/*
PostgreSQL SSL Mode
*/}}
{{- define "tracecat.postgres.sslMode" -}}
{{- .Values.externalPostgres.sslMode | default "prefer" }}
{{- end }}

{{/*
PostgreSQL TLS CA ConfigMap Name
Returns the name of the ConfigMap containing the CA certificate for TLS verification
*/}}
{{- define "tracecat.postgres.caConfigMapName" -}}
{{- if and .Values.externalPostgres.tls.verifyCA .Values.externalPostgres.tls.caCert }}
{{- printf "%s-postgres-ca" (include "tracecat.fullname" .) }}
{{- end }}
{{- end }}

{{/*
PostgreSQL TLS CA Certificate Path
Returns the mount path for the CA certificate file
*/}}
{{- define "tracecat.postgres.caCertPath" -}}
{{- "/etc/tracecat/certs/postgres/ca-bundle.pem" }}
{{- end }}

{{/*
PostgreSQL TLS CA Volume
Returns the volume definition for mounting the CA certificate
*/}}
{{- define "tracecat.postgres.caVolume" -}}
{{- if and .Values.externalPostgres.tls.verifyCA (include "tracecat.postgres.caConfigMapName" .) }}
- name: postgres-ca
  configMap:
    name: {{ include "tracecat.postgres.caConfigMapName" . }}
    items:
      - key: ca-bundle.pem
        path: ca-bundle.pem
{{- end }}
{{- end }}

{{/*
PostgreSQL TLS CA Volume Mount
Returns the volume mount definition for the CA certificate
*/}}
{{- define "tracecat.postgres.caVolumeMount" -}}
{{- if and .Values.externalPostgres.tls.verifyCA (include "tracecat.postgres.caConfigMapName" .) }}
- name: postgres-ca
  mountPath: /etc/tracecat/certs/postgres
  readOnly: true
{{- end }}
{{- end }}

{{/*
=============================================================================
Redis/Valkey Helpers
=============================================================================
*/}}

{{/*
=============================================================================
URL Helpers
=============================================================================
*/}}

{{/*
URL scheme - returns https if TLS is configured, http otherwise
*/}}
{{- define "tracecat.urlScheme" -}}
{{- if .Values.ingress.tls }}https{{- else }}http{{- end }}
{{- end }}

{{/*
Public App URL - used for browser redirects and public-facing links
*/}}
{{- define "tracecat.publicAppUrl" -}}
{{- if .Values.urls.publicApp }}
{{- .Values.urls.publicApp }}
{{- else }}
{{- printf "%s://%s" (include "tracecat.urlScheme" .) .Values.ingress.host }}
{{- end }}
{{- end }}

{{/*
Public API URL - used for external API access
*/}}
{{- define "tracecat.publicApiUrl" -}}
{{- if .Values.urls.publicApi }}
{{- .Values.urls.publicApi }}
{{- else }}
{{- printf "%s://%s/api" (include "tracecat.urlScheme" .) .Values.ingress.host }}
{{- end }}
{{- end }}

{{/*
Public S3 URL - used for presigned URLs
*/}}
{{- define "tracecat.publicS3Url" -}}
{{- if .Values.urls.publicS3 }}
{{- .Values.urls.publicS3 }}
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
{{- else }}
{{- .Values.externalS3.endpoint }}
{{- end }}
{{- end }}

{{/*
Temporal Fullname - mirrors the subchart naming logic
*/}}
{{- define "tracecat.temporalFullname" -}}
{{- if .Values.temporal.fullnameOverride }}
{{- .Values.temporal.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default "temporal" .Values.temporal.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Temporal Cluster URL - supports both subchart and external Temporal
*/}}
{{- define "tracecat.temporalClusterUrl" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- $port := dig "temporal" "server" "frontend" "service" "port" 7233 $values -}}
{{- printf "%s-frontend:%v" (include "tracecat.temporalFullname" .) $port }}
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
{{- $values := .Values | toYaml | fromYaml -}}
{{- $namespaces := dig "temporal" "server" "config" "namespaces" "namespace" list $values -}}
{{- if and $namespaces (gt (len $namespaces) 0) -}}
{{- $namespace := index $namespaces 0 -}}
{{- index $namespace "name" | default "default" -}}
{{- else -}}
{{- "default" -}}
{{- end }}
{{- else if .Values.externalTemporal.enabled }}
{{- .Values.externalTemporal.clusterNamespace | default "default" }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{/*
Temporal Namespace Retention
*/}}
{{- define "tracecat.temporalNamespaceRetention" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- $namespaces := dig "temporal" "server" "config" "namespaces" "namespace" list $values -}}
{{- if and $namespaces (gt (len $namespaces) 0) -}}
{{- $namespace := index $namespaces 0 -}}
{{- index $namespace "retention" | default "720h" -}}
{{- else -}}
{{- "720h" -}}
{{- end }}
{{- else }}
{{- "720h" -}}
{{- end }}
{{- end }}

{{/*
Temporal Namespace History Archival State
*/}}
{{- define "tracecat.temporalNamespaceHistoryArchivalState" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- dig "temporal" "server" "namespaceDefaults" "archival" "history" "state" "" $values -}}
{{- else -}}
{{- "" -}}
{{- end }}
{{- end }}

{{/*
Temporal Namespace History Archival URI
*/}}
{{- define "tracecat.temporalNamespaceHistoryArchivalURI" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- dig "temporal" "server" "namespaceDefaults" "archival" "history" "URI" "" $values -}}
{{- else -}}
{{- "" -}}
{{- end }}
{{- end }}

{{/*
Temporal Namespace Visibility Archival State
*/}}
{{- define "tracecat.temporalNamespaceVisibilityArchivalState" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- dig "temporal" "server" "namespaceDefaults" "archival" "visibility" "state" "" $values -}}
{{- else -}}
{{- "" -}}
{{- end }}
{{- end }}

{{/*
Temporal Namespace Visibility Archival URI
*/}}
{{- define "tracecat.temporalNamespaceVisibilityArchivalURI" -}}
{{- if .Values.temporal.enabled }}
{{- $values := .Values | toYaml | fromYaml -}}
{{- dig "temporal" "server" "namespaceDefaults" "archival" "visibility" "URI" "" $values -}}
{{- else -}}
{{- "" -}}
{{- end }}
{{- end }}

{{/*
Temporal Queue
*/}}
{{- define "tracecat.temporalQueue" -}}
{{- if .Values.externalTemporal.enabled }}
{{- .Values.externalTemporal.clusterQueue | default "tracecat-task-queue" -}}
{{- else if .Values.temporal.enabled }}
{{- .Values.temporal.clusterQueue | default "tracecat-task-queue" -}}
{{- else }}
{{- "tracecat-task-queue" -}}
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
{{- define "tracecat.featureFlags" -}}
{{- $flags := list -}}
{{- if .Values.tracecat.featureFlags }}
{{- $flags = append $flags .Values.tracecat.featureFlags -}}
{{- end }}
{{- if .Values.enterprise.featureFlags }}
{{- $flags = append $flags .Values.enterprise.featureFlags -}}
{{- end }}
{{- join "," $flags -}}
{{- end }}

{{- define "tracecat.env.common" -}}
{{- if .Values.tracecat.logLevel }}
- name: LOG_LEVEL
  value: {{ .Values.tracecat.logLevel | quote }}
{{- end }}
- name: TRACECAT__APP_ENV
  value: {{ .Values.tracecat.appEnv | quote }}
- name: TRACECAT__FEATURE_FLAGS
  value: {{ include "tracecat.featureFlags" . | quote }}
- name: TRACECAT__EE_MULTI_TENANT
  value: {{ .Values.enterprise.multiTenant | quote }}
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
{{- if .Values.tracecat.blobStorage.buckets.workflow }}
- name: TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW
  value: {{ .Values.tracecat.blobStorage.buckets.workflow | quote }}
{{- end }}
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

{{/*
PostgreSQL environment variables
Constructs TRACECAT__DB_URI from computed host/port/database/sslmode and secret credentials
*/}}
{{- define "tracecat.env.postgres" -}}
{{- $host := include "tracecat.postgres.host" . }}
{{- $port := include "tracecat.postgres.port" . }}
{{- $database := include "tracecat.postgres.database" . }}
{{- $sslMode := include "tracecat.postgres.sslMode" . }}
{{- $hasSecretArn := .Values.externalPostgres.auth.secretArn }}
{{- $postgresSecretName := include "tracecat.secrets.postgresName" . }}
{{- $hasExistingSecret := $postgresSecretName }}
{{- if $hasSecretArn }}
{{- if .Values.externalPostgres.auth.username }}
- name: TRACECAT__DB_USER
  value: {{ .Values.externalPostgres.auth.username | quote }}
{{- else if $hasExistingSecret }}
- name: TRACECAT__DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ $postgresSecretName }}
      key: username
{{- end }}
- name: TRACECAT__DB_PASS__ARN
  value: {{ .Values.externalPostgres.auth.secretArn | quote }}
{{- end }}
{{- if $hasExistingSecret }}
{{- if not $hasSecretArn }}
- name: TRACECAT__DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ $postgresSecretName }}
      key: username
{{- end }}
- name: TRACECAT__DB_PASS
  valueFrom:
    secretKeyRef:
      name: {{ $postgresSecretName }}
      key: password
{{- end }}
- name: TRACECAT__DB_ENDPOINT
  value: {{ $host | quote }}
- name: TRACECAT__DB_PORT
  value: {{ $port | quote }}
- name: TRACECAT__DB_NAME
  value: {{ $database | quote }}
- name: TRACECAT__DB_SSLMODE
  value: {{ $sslMode | quote }}
{{- if not (or $hasSecretArn $hasExistingSecret) }}
{{- fail "externalPostgres.auth.existingSecret, externalPostgres.auth.secretArn, or secrets.create.postgres.enabled is required" }}
{{- end }}
{{- end }}

{{/*
Redis environment variables
Constructs REDIS_URL from computed host/port or from external secret
*/}}
{{- define "tracecat.env.redis" -}}
{{- $redisSecretName := include "tracecat.secrets.redisName" . }}
{{- if .Values.externalRedis.auth.secretArn }}
- name: REDIS_URL__ARN
  value: {{ .Values.externalRedis.auth.secretArn | quote }}
{{- else if $redisSecretName }}
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ $redisSecretName }}
      key: url
{{- else }}
{{- fail "externalRedis.auth.existingSecret, externalRedis.auth.secretArn, or secrets.create.redis.enabled is required" }}
{{- end }}
{{- end }}

{{/*
Sandbox environment variables (shared by executor + agent executor)
*/}}
{{- define "tracecat.env.sandbox" -}}
- name: TRACECAT__DISABLE_NSJAIL
  value: {{ .Values.tracecat.sandbox.disableNsjail | quote }}
- name: TRACECAT__SANDBOX_NSJAIL_PATH
  value: "/usr/local/bin/nsjail"
- name: TRACECAT__SANDBOX_ROOTFS_PATH
  value: "/var/lib/tracecat/sandbox-rootfs"
- name: TRACECAT__SANDBOX_CACHE_DIR
  value: "/var/lib/tracecat/sandbox-cache"
- name: TRACECAT__UNSAFE_DISABLE_SM_MASKING
  value: "false"
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
{{- end }}
{{- /* OIDC settings */}}
{{- if .Values.tracecat.oidc.issuer }}
- name: OIDC_ISSUER
  value: {{ .Values.tracecat.oidc.issuer | quote }}
{{- end }}
{{- if .Values.tracecat.oidc.clientId }}
- name: OIDC_CLIENT_ID
  value: {{ .Values.tracecat.oidc.clientId | quote }}
{{- end }}
{{- if .Values.tracecat.oidc.clientSecret }}
- name: OIDC_CLIENT_SECRET
  value: {{ .Values.tracecat.oidc.clientSecret | quote }}
{{- end }}
{{- if .Values.tracecat.oidc.scopes }}
- name: OIDC_SCOPES
  value: {{ .Values.tracecat.oidc.scopes | quote }}
{{- end }}
{{- end }}

{{/*
Worker service environment variables
Merges: common + temporal + postgres + redis + worker-specific
*/}}
{{- define "tracecat.env.worker" -}}
{{ include "tracecat.env.common" . }}
{{ include "tracecat.env.temporal" . }}
{{ include "tracecat.env.blobStorage" . }}
{{ include "tracecat.env.postgres" . }}
{{ include "tracecat.env.redis" . }}
{{- if .Values.tracecat.temporal.metrics.enabled }}
- name: TEMPORAL__METRICS_PORT
  value: {{ .Values.tracecat.temporal.metrics.port | quote }}
{{- end }}
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
{{- if .Values.tracecat.temporal.metrics.enabled }}
- name: TEMPORAL__METRICS_PORT
  value: {{ .Values.tracecat.temporal.metrics.port | quote }}
{{- end }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.executor.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.executor.contextCompression.thresholdKb | quote }}
{{- /* Sandbox + secret masking */}}
{{ include "tracecat.env.sandbox" . }}
{{- /* Executor settings */}}
- name: TRACECAT__EXECUTOR_BACKEND
  value: {{ .Values.executor.backend | quote }}
- name: TRACECAT__EXECUTOR_QUEUE
  value: {{ .Values.executor.queue | quote }}
- name: TRACECAT__EXECUTOR_WORKER_POOL_SIZE
  value: {{ .Values.executor.workerPoolSize | quote }}
{{- end }}

{{/*
Agent Executor service environment variables
Merges: common + temporal + postgres + redis + agent-executor-specific
*/}}
{{- define "tracecat.env.agentExecutor" -}}
{{ include "tracecat.env.common" . }}
{{ include "tracecat.env.temporal" . }}
{{ include "tracecat.env.blobStorage" . }}
{{ include "tracecat.env.postgres" . }}
{{ include "tracecat.env.redis" . }}
{{- if .Values.tracecat.temporal.metrics.enabled }}
- name: TEMPORAL__METRICS_PORT
  value: {{ .Values.tracecat.temporal.metrics.port | quote }}
{{- end }}
- name: TRACECAT__API_URL
  value: {{ include "tracecat.internalApiUrl" . | quote }}
{{- /* Context compression */}}
- name: TRACECAT__CONTEXT_COMPRESSION_ENABLED
  value: {{ .Values.agentExecutor.contextCompression.enabled | quote }}
- name: TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB
  value: {{ .Values.agentExecutor.contextCompression.thresholdKb | quote }}
{{- /* Sandbox + secret masking */}}
{{ include "tracecat.env.sandbox" . }}
{{- /* Agent executor settings */}}
- name: TRACECAT__EXECUTOR_BACKEND
  value: {{ .Values.agentExecutor.backend | quote }}
- name: TRACECAT__AGENT_QUEUE
  value: {{ .Values.agentExecutor.queue | quote }}
- name: TRACECAT__EXECUTOR_WORKER_POOL_SIZE
  value: {{ .Values.agentExecutor.workerPoolSize | quote }}
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
ESO-Aware Secret Name Resolution
=============================================================================
*/}}

{{/*
Get the effective core secrets name.
When ESO is enabled with coreSecrets, use the ESO target name.
Otherwise, require the manual existingSecret.
*/}}
{{- define "tracecat.secrets.coreName" -}}
{{- if and .Values.externalSecrets.enabled .Values.externalSecrets.coreSecrets.enabled .Values.externalSecrets.coreSecrets.secretArn }}
{{- .Values.externalSecrets.coreSecrets.targetSecretName }}
{{- else if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else if .Values.secrets.create.tracecat.enabled }}
{{- required "secrets.create.tracecat.name is required when tracecat secret template is enabled" .Values.secrets.create.tracecat.name }}
{{- else }}
{{- fail "Either secrets.existingSecret, secrets.create.tracecat.enabled, or externalSecrets.coreSecrets (with secretArn) must be configured" }}
{{- end }}
{{- end }}

{{/*
Get the effective PostgreSQL secrets name for external Postgres.
ESO-managed secret takes precedence over existingSecret.
*/}}
{{- define "tracecat.secrets.postgresName" -}}
{{- if and .Values.externalSecrets.enabled .Values.externalSecrets.postgres.enabled .Values.externalSecrets.postgres.secretArn }}
{{- .Values.externalSecrets.postgres.targetSecretName }}
{{- else if .Values.externalPostgres.auth.existingSecret }}
{{- .Values.externalPostgres.auth.existingSecret }}
{{- else if .Values.secrets.create.postgres.enabled }}
{{- required "secrets.create.postgres.name is required when postgres secret template is enabled" .Values.secrets.create.postgres.name }}
{{- end }}
{{- end }}

{{/*
Get the effective Redis secrets name for external Redis.
*/}}
{{- define "tracecat.secrets.redisName" -}}
{{- if and .Values.externalSecrets.enabled .Values.externalSecrets.redis.enabled .Values.externalSecrets.redis.secretArn }}
{{- .Values.externalSecrets.redis.targetSecretName }}
{{- else if .Values.externalRedis.auth.existingSecret }}
{{- .Values.externalRedis.auth.existingSecret }}
{{- else if .Values.secrets.create.redis.enabled }}
{{- required "secrets.create.redis.name is required when redis secret template is enabled" .Values.secrets.create.redis.name }}
{{- end }}
{{- end }}

{{/*
Get the effective Temporal secrets name for external Temporal.
*/}}
{{- define "tracecat.secrets.temporalName" -}}
{{- if and .Values.externalSecrets.enabled .Values.externalSecrets.temporal.enabled .Values.externalSecrets.temporal.secretArn }}
{{- .Values.externalSecrets.temporal.targetSecretName }}
{{- else if .Values.externalTemporal.auth.existingSecret }}
{{- .Values.externalTemporal.auth.existingSecret }}
{{- end }}
{{- end }}

{{/*
=============================================================================
Validation Helpers
=============================================================================
*/}}

{{/*
Validate required secrets - accepts either manual secret or ESO-managed secret
*/}}
{{- define "tracecat.validateRequiredSecrets" -}}
{{- $hasManualSecret := .Values.secrets.existingSecret -}}
{{- $hasTemplatedSecret := .Values.secrets.create.tracecat.enabled -}}
{{- $hasEsoSecret := and .Values.externalSecrets.enabled .Values.externalSecrets.coreSecrets.enabled .Values.externalSecrets.coreSecrets.secretArn -}}
{{- if not (or $hasManualSecret $hasTemplatedSecret $hasEsoSecret) -}}
{{- fail "Core secrets required: set secrets.existingSecret, enable secrets.create.tracecat, or enable externalSecrets with coreSecrets.secretArn" -}}
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
{{- define "tracecat.validateTemporalSqlStore" -}}
{{- $storeName := .storeName -}}
{{- $storeConfig := .storeConfig -}}
{{- if not $storeConfig -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql is required when temporal.enabled=true" $storeName) -}}
{{- end -}}
{{- $pluginName := dig "pluginName" "" $storeConfig -}}
{{- if and (ne $pluginName "postgres12") (ne $pluginName "postgres12_pgx") -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql.pluginName must be postgres12 or postgres12_pgx" $storeName) -}}
{{- end -}}
{{- if not (dig "connectAddr" "" $storeConfig) -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql.connectAddr is required when temporal.enabled=true" $storeName) -}}
{{- end -}}
{{- if not (dig "databaseName" "" $storeConfig) -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql.databaseName is required when temporal.enabled=true" $storeName) -}}
{{- end -}}
{{- if not (dig "user" "" $storeConfig) -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql.user is required when temporal.enabled=true" $storeName) -}}
{{- end -}}
{{- if not (or (dig "existingSecret" "" $storeConfig) (dig "password" "" $storeConfig)) -}}
{{- fail (printf "temporal.server.config.persistence.datastores.%s.sql.existingSecret (or sql.password) is required when temporal.enabled=true" $storeName) -}}
{{- end -}}
{{- end -}}

{{- define "tracecat.validateInfrastructure" -}}
{{- if and (not .Values.temporal.enabled) (not .Values.externalTemporal.enabled) -}}
{{- fail "Either temporal.enabled or externalTemporal.enabled must be true" -}}
{{- end -}}
{{- if .Values.temporal.enabled -}}
{{- $values := .Values | toYaml | fromYaml -}}
{{- $defaultSql := dig "temporal" "server" "config" "persistence" "datastores" "default" "sql" nil $values -}}
{{- $visibilitySql := dig "temporal" "server" "config" "persistence" "datastores" "visibility" "sql" nil $values -}}
{{- include "tracecat.validateTemporalSqlStore" (dict "storeName" "default" "storeConfig" $defaultSql) -}}
{{- include "tracecat.validateTemporalSqlStore" (dict "storeName" "visibility" "storeConfig" $visibilitySql) -}}
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Secret Environment Variable Helpers
=============================================================================
*/}}

{{/*
Secret environment variables (shared by api, worker, executor)
Uses ESO-aware secret name resolution.
*/}}
{{- define "tracecat.env.secrets" -}}
{{- $secretName := include "tracecat.secrets.coreName" . }}
- name: TRACECAT__DB_ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: dbEncryptionKey
- name: TRACECAT__SERVICE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: serviceKey
- name: TRACECAT__SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: signingSecret
{{- end -}}

{{/*
API-specific secret env vars (user auth)
Uses ESO-aware secret name resolution.
*/}}
{{- define "tracecat.env.secrets.api" -}}
{{- $coreSecretName := include "tracecat.secrets.coreName" . }}
{{ include "tracecat.env.secrets" . }}
- name: USER_AUTH_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $coreSecretName }}
      key: userAuthSecret
{{- end -}}

{{/*
UI-specific secret env vars
Uses ESO-aware secret name resolution.
*/}}
{{- define "tracecat.env.secrets.ui" -}}
{{- $secretName := include "tracecat.secrets.coreName" . }}
- name: TRACECAT__SERVICE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: serviceKey
{{- end -}}
