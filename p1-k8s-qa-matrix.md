# P1 Kubernetes Workload Failure QA Matrix

## Preflight

| ID | Check | Expected | Status | Notes |
| --- | --- | --- | --- | --- |
| QA-00 | Open `http://127.0.0.1:9090/alerts` | The `tracecat-workload-failures` group lists the nine P1 alerts. | PASS | Prometheus API lists all nine P1 alerts, including `TracecatContainerOOMKilledCapacityDegraded`. |
| QA-01 | Check Tracecat pod labels in Prometheus | `kube_pod_labels{label_app_kubernetes_io_part_of="tracecat"}` has series for Tracecat pods. | PASS | Pod label series present for api, ui, litellm, worker, agent-worker, executor, and agent-executor. |
| QA-02 | Check Tracecat deployment labels in Prometheus | `kube_deployment_labels{label_app_kubernetes_io_part_of="tracecat"}` has series for Tracecat deployments. | PASS | Deployment label series present for api, ui, litellm, worker, agent-worker, executor, and agent-executor without depending on Helm release name. |
| QA-03 | Baseline alert state | No P1 alerts are firing before QA fixtures are created. Existing unrelated alerts should be noted before testing. | PASS | All nine P1 alerts inactive before fixtures; no `qa.tracecat.com/p1=true` resources exist. |

## Test Matrix

| ID | Alert | Scenario | Trigger | Expected | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| QA-10 | `TracecatContainerOOMKilledObserved` | Single OOM on a Tracecat-labeled runtime pod | Create a QA deployment whose container OOMs once, then restarts and sleeps | Warning alert fires. `TracecatContainerOOMKilledRepeated` should not fire if there is only one restart. | PASS | `qa-p1-oom-single-litellm` fired `TracecatContainerOOMKilledObserved`; repeated OOM did not include the one-restart LiteLLM pod. |
| QA-11 | `TracecatContainerOOMKilledRepeated` | Repeated OOM-like failure on a non-ephemeral component | Create a QA deployment labeled as `api`, `worker`, etc. whose container repeatedly OOMs | Critical alert fires after restart count reaches `>=2` in `30m`. `TracecatRuntimeContainerCrashLooping` may also become pending/firing after its `for: 5m` window. | PASS | `qa-p1-oom-worker` fired repeated OOM after Prometheus observed `increase(restarts_total[30m]) >= 2`; runtime crashlooping also entered pending for the same pod. |
| QA-12 | `TracecatContainerOOMKilledRepeated` | Repeated OOM-like failure on `executor` or `agent-executor` | Create a repeatedly OOMing QA deployment labeled as `executor` | Critical repeated-OOM alert fires for execution-capacity components. `TracecatRuntimeContainerCrashLooping` may also become pending/firing after its `for: 5m` window. | PASS | `qa-p1-oom-executor` fired repeated OOM with component `executor`, confirming the updated detection 2 scope includes execution-capacity components. |
| QA-13 | `TracecatContainerOOMKilledCapacityDegraded` | First OOM on a non-executor component while capacity is degraded | Create a one-restart OOM QA deployment labeled as `api` with desired replicas `1` and no available replicas, such as a pod that OOMs once, restarts, then remains unready | Critical alert fires because OOM is paired with degraded deployment capacity. `TracecatContainerOOMKilledRepeated` should not fire if there is only one restart. | PASS | `qa-p1-oom-degraded-api` OOMed once and remained unavailable through a failing readiness probe; capacity-degraded alert fired for `api`. The same alert also fired for `worker` because repeated worker OOM degraded that QA deployment. |
| QA-20 | `TracecatRuntimeContainerCrashLooping` | Runtime component repeatedly exits non-zero | Create a QA deployment labeled as `api`, `worker`, `executor`, etc. whose container exits `1` repeatedly | Critical alert becomes pending after `>2` restarts in `15m`, then firing after `5m` continuously true. | PASS | `qa-p1-crash-ui` fired `TracecatRuntimeContainerCrashLooping` after the 5-minute `for` window. The repeated OOM worker/executor fixtures also satisfied the runtime crashloop symptom. |
| QA-21 | `TracecatReleaseLifecycleCrashLooping` | Migration container repeatedly exits non-zero | Create a QA Job/Pod labeled `migrations` whose container exits `1` repeatedly | Warning alert becomes pending after `>2` restarts in `15m`, then firing after `5m`. Runtime crashlooping alert should not match `migrations`. | PASS | `qa-p1-crash-migrations` fired `TracecatReleaseLifecycleCrashLooping`; `TracecatRuntimeContainerCrashLooping` did not include the migrations pod. |
| QA-22 | CrashLoopBackOff startup exclusion | Crashlooping container enters `CrashLoopBackOff` | Use the QA-20 fixture | `TracecatRuntimeContainerCrashLooping` fires. `TracecatContainerStartupBlocked` should not fire solely for `CrashLoopBackOff`. | PASS | Crashlooping fixtures fired crashloop alerts, while `TracecatContainerStartupBlocked` contained only the bad-image `qa-p1-startup-api` and `qa-p1-startup-executor` pods. |
| QA-30 | `TracecatContainerStartupBlocked` | Pod cannot start because of image/config/pull issue | Create a Tracecat-labeled QA pod with a bad image or invalid image name | Warning alert becomes pending immediately after the expression matches, then firing after `10m`. | PASS | `qa-p1-startup-api` and `qa-p1-startup-executor` fired StartupBlocked after the 10-minute `for` window with reason `ImagePullBackOff`. |
| QA-31 | `TracecatRolloutBlocked` | Non-ephemeral deployment rollout is blocked and has degraded replicas | Create a QA deployment labeled as `api`, `worker`, etc. with a bad image and desired replicas `>0` | Critical alert becomes pending while startup is blocked and available replicas are below desired, then firing after `10m`. | PASS | Bad-image API deployment had startup-blocked pods and available replicas below desired; RolloutBlocked fired for component `api`. |
| QA-32 | Rollout scope exclusion | Execution-capacity deployment has startup-blocked pods | Create a bad-image QA deployment labeled `executor` | `TracecatContainerStartupBlocked` fires. `TracecatRolloutBlocked` should not fire for `executor` in v0. | PASS | Bad-image executor deployment fired StartupBlocked, but RolloutBlocked fired only for `api`, not `executor`. |
| QA-40 | `TracecatPodEvictedObserved` | Single Tracecat pod is evicted | Create a QA pod with tight ephemeral-storage limit that writes past the limit | Warning alert fires once `kube_pod_status_reason{reason="Evicted"}` appears. | PASS | `qa-p1-evict-api-1`, `qa-p1-evict-api-2`, and `qa-p1-evict-migrations` reached pod reason `Evicted`; observed alert fired. |
| QA-41 | `TracecatPodEvictionStorm` | Repeated pod evictions in a runtime component | Create two evicted QA pods with the same runtime component label within `30m` | Critical alert becomes pending after the storm expression matches, then firing after `5m`. | PASS | Two API eviction pods caused `TracecatPodEvictionStorm` to fire for component `api` after the 5-minute `for` window. |
| QA-42 | Eviction scope exclusion | Migration pod is evicted | Create an evicted QA pod labeled `migrations` | `TracecatPodEvictedObserved` fires. `TracecatPodEvictionStorm` should not fire for `migrations`. | PASS | `qa-p1-evict-migrations` appeared in `TracecatPodEvictedObserved`; `TracecatPodEvictionStorm` fired only for `api`, not `migrations`. |
| QA-50 | Non-Tracecat noise exclusion | Unrelated pod has image pull error, crashloop, OOM, or eviction | Create the same failure without Tracecat labels | No P1 Tracecat alert should match. | PASS | `qa-p1-noise-startup` entered image pull failure without Tracecat labels; no P1 alert contained that pod. |

## Local Validation

Use Prometheus UI:

```text
http://127.0.0.1:9090/alerts
```

Use the API to list loaded P1 alert names:

```bash
curl -sS 'http://127.0.0.1:9090/api/v1/rules?type=alert' \
  | jq -r '.data.groups[].rules[].name | select(startswith("Tracecat"))'
```

Use the API to check a specific alert state:

```bash
curl -sS 'http://127.0.0.1:9090/api/v1/rules?type=alert' \
  | jq '.data.groups[].rules[] | select(.name == "TracecatRuntimeContainerCrashLooping") | {name,state,alerts}'
```

## Timing Notes

```text
No for: alert can fire on the next rule evaluation once the expression is true.
for: 5m: alert should go pending first, then firing after 5 continuous minutes.
for: 10m: alert should go pending first, then firing after 10 continuous minutes.
```

Prometheus evaluates these rules every `60s` locally, so allow roughly one extra minute beyond the `for:` duration before calling a test failed.

## Cleanup

All QA fixtures should carry this label:

```text
qa.tracecat.com/p1=true
```

Cleanup command:

```bash
kubectl delete pod,deploy,job -n tracecat -l qa.tracecat.com/p1=true --ignore-not-found
```
