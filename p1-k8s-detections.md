# P1 Kubernetes Workload Failure Detections

## Service Groups

```text
request-path:
api, ui, mcp, litellm

workflow-path:
worker, agent-worker

execution-capacity:
executor, agent-executor

release-lifecycle:
migrations
```

## Severity Model

```text
critical
Interrupt someone. Page.
Must be urgent, actionable, high-confidence, and user-visible or imminently user-visible.
Expected response: investigate now, mitigate now, file/fix root cause.

warning
Do not interrupt. Create ticket / Slack non-page / dashboard alert.
Important enough that ignoring it forever would be wrong, but not urgent enough to wake someone.
Expected response: triage next business day or during the next on-call work block.

info
No alert delivery.
Useful diagnostic context for dashboards, deploy review, incident debugging, trend analysis.
Expected response: none unless correlated with another alert.
```

## Detections

1. `TracecatContainerOOMKilledObserved`

```text
severity: warning
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor, migrations
condition: OOMKilled observed in last 15m
page: no
```

2. `TracecatContainerOOMKilledRepeated`

```text
severity: critical
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor
condition: OOMKilled-like termination + >=2 restarts in 30m
page: yes
```

3. `TracecatContainerOOMKilledCapacityDegraded`

```text
severity: critical
applies to: api, ui, mcp, litellm, worker, agent-worker
condition: OOMKilled observed in last 15m AND deployment available replicas are below desired replicas
page: yes
why: a first OOM is page-worthy when it coincides with degraded serving/control capacity; otherwise first OOM remains a warning detection
```

4. `TracecatRuntimeContainerCrashLooping`

```text
severity: critical
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor
condition: >2 restarts in 15m for 5m
page: yes
why: runtime workloads should not repeatedly fail; OOMKilled may be one cause, but crashlooping is the repeated-failure symptom
```

5. `TracecatReleaseLifecycleCrashLooping`

```text
severity: warning
applies to: migrations
condition: >2 restarts in 15m for 5m
page: no in steady-state runtime monitoring
note: the deploy pipeline or Helm release health check should separately fail hard on migration failure
```

6. `TracecatContainerStartupBlocked`

```text
severity: warning
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor, migrations
condition: waiting reason for 10m
reasons: CreateContainerConfigError, CreateContainerError, ErrImagePull, ImagePullBackOff, InvalidImageName
exclude: CrashLoopBackOff
page: no
```

7. `TracecatRolloutBlocked`

```text
severity: critical
applies to: api, ui, mcp, litellm, worker, agent-worker
condition: startup blocked while replicas are degraded for 10m
page: yes
```

8. `TracecatPodEvictedObserved`

```text
severity: warning
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor, migrations
condition: any pod eviction observed
page: no
```

9. `TracecatPodEvictionStorm`

```text
severity: critical
applies to: api, ui, mcp, litellm, worker, agent-worker, executor, agent-executor
condition: >=2 evicted pods in 30m OR eviction + degraded replicas for 5m
page: yes
why: repeated eviction means systemic resource pressure or capacity loss, including execution-capacity loss
```

## V0 Metric Notes

For OOMKilled, v0 uses kube-state-metrics because `container_oom_events_total` is not available in the local OrbStack/Alloy/Prometheus setup.

```text
observed OOM:
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} over 15m

repeated OOM-like:
last termination reason is OOMKilled AND restarts increased over 30m

OOM plus degraded capacity:
last termination reason is OOMKilled over 15m AND deployment available replicas are below desired replicas
```

Deployment-capacity joins should select Tracecat deployments with
`label_app_kubernetes_io_part_of="tracecat"` plus component labels, not fixed
`app.kubernetes.io/name` or `app.kubernetes.io/instance` values. Release names
and name overrides must not silently disable capacity-gated alerts.

This is an approximation, not an exact OOM event counter.

Example:

```text
12:00 container exits with Error
12:10 container exits with OOMKilled
12:20 alert checks
```

The container had two restarts and its last termination reason is OOMKilled, so
the repeated OOM-like rule can match even though only one restart was actually
an OOM.

# P2 Request Error Detections

## V0 Metric Shape

```text
tracecat_http_requests_total{
  service="api|mcp|litellm",
  route="/api/workflows/{id}",
  method="GET",
  status_code="500",
  status_class="5xx"
}
```

```text
Do not put user IDs, workspace IDs, request IDs, or raw URLs in Prometheus labels.
Routes should be normalized.
Sentry should carry high-cardinality debugging context such as release, exception, user/workspace, and request details.
```

## Detections

1. `TracecatHigh5xxRate`

```text
severity: critical
applies to: api, mcp
condition: 5xx ratio > 5% for 5m with traffic floor
page: yes
why: request-path server errors are likely user-visible and actionable
sentry: link/filter by service, route, release, environment, unhandled exceptions
```

2. `TracecatElevated5xxRate`

```text
severity: warning
applies to: api, mcp
condition: 5xx ratio > 2% for 15m with traffic floor
page: no
why: worth investigating, but not urgent enough to interrupt
sentry: link/filter by service, route, release, environment, unhandled exceptions
```

3. `Tracecat4xxAnomaly`

```text
severity: warning
applies to: api, mcp
condition: selected 4xx ratio spike for 10-15m with traffic floor
status codes: 400, 401, 403, 422, 429
page: no
why: useful regression, abuse, auth, or API-contract signal, but generic 4xx is not page-worthy
sentry: link/filter by service, route, status_code, release, environment
```

4. `TracecatLiteLLMProxyErrorRate`

```text
severity: warning
applies to: litellm
condition: proxy/platform errors elevated for 10-15m
page: no by default
why: LiteLLM 5xx/429 is often upstream provider behavior, not directly actionable
page later only if: proxy unavailable, all providers unusable, or no fallback path
sentry: link/filter by provider, model, proxy error source, route, release, environment
```
