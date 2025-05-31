{{/* vim: set filetype=mustache: */}}

{{/*
Set the chart fullname
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the labels spec).
We change "+" with "_" for OCI compatibility
*/}}
{{- define "chart.fullname" -}}
{{- printf "%s-%s" .Chart.Name (.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-") -}}
{{- end }}

{{/*
Set the chart version
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the labels spec).
We change "+" with "_" for OCI compatibility
*/}}
{{- define "chart.version" -}}
{{- .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "tracecat.image" -}}
{{ (.image).repository | default "ghcr.io/tracecathq/tracecat" }}:{{ (.image).tag | default "0.31.4" }}
{{- end }}

{{- define "tracecat.auth.types" -}}
{{- $value := list "basic" "google_oauth" "saml" }}
{{- if not .Values.auth.basic.enabled }}
{{- $value := without $value "basic" }}
{{- end}}
{{- if not .Values.auth.google_oauth.enabled }}
{{- $value := without $value "google_oauth" }}
{{- end}}
{{- if not .Values.auth.saml.enabled }}
{{- $value := without $value "saml" }}
{{- end}}
{{- join "," $value }}
{{- end -}}

{{- define "tracecat.services.api" -}}
{{ .Values.api.service.name }}:{{ .Values.api.service.port }}
{{- end }}

{{- define "tracecat.services.executor" -}}
{{ .Values.executor.service.name }}:{{ .Values.executor.service.port }}
{{- end }}

{{- define "tracecat.services.temporal" -}}
{{- if .Values.temporal.enabled -}}
{{ .Values.temporal.service.name }}:{{ .Values.temporal.service.port }}
{{- else -}}
{{ .Values.temporal.cluster_address }}
{{- end }}

{{- define "tracecat.services.ui" -}}
{{ .Values.ui.service.name }}:{{ .Values.ui.service.port }}
{{- end }}

{{- define "tracecat.services.worker" -}}
{{ .Values.worker.service.name }}:{{ .Values.worker.service.port }}
{{- end }}

{{- define "tracecat.url.internal_api_url" -}}
http://{{- include "tracecat.services.api" . }}
{{- end }}

{{- define "tracecat.url.internal_app_url" -}}
http://{{- include "tracecat.services.ui" . }}
{{- end }}

{{- define "tracecat.url.public_api_url" -}}
{{- if .Values.ingress.tls -}}
https://{{ .Values.ingress.hostname }}{{ .Values.api.root_path }}
{{- else -}}
http://{{ .Values.ingress.hostname }}{{ .Values.api.root_path }}
{{- end -}}
{{- end }}

{{- define "tracecat.url.public_app_url" -}}
{{- if .Values.ingress.tls -}}
https://{{ .Values.ingress.hostname }}
{{- else -}}
http://{{ .Values.ingress.hostname }}
{{- end }}
{{- end }}