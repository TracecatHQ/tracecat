# API simulation for agent and workflow development

## Summary

We want a development feature that lets Tracecat behave as if it is talking to real external APIs while actually returning controlled mock responses. The product goal is to make agent testing, MCP-driven workflow authoring, and action development feel real without needing the target system to exist yet.

There are two materially different options:

1. `core.http_request` simulation only
2. Broad outbound HTTP simulation via a proxy or interceptor

They are not incremental versions of the same implementation. The first is a tool-layer feature. The second is an execution-runtime feature.

If the simulator is LLM-backed, raw request latency is not the deciding argument between these shapes. The better comparison is:

- how explicit the simulation boundary is
- how deterministic and debuggable the behavior is
- whether request-scoped simulation state can be carried safely through executor backends
- whether the hosting form factor can support the connection semantics required by the design

Current operating assumption for this evaluation:

- draft and development executions use the `direct` and `ephemeral` executor paths
- warm pool executors are not in current use for this feature
- if warm pool is adopted later, draft/dev executions can remain on `direct` or `ephemeral`

Under that assumption, broad proxy-style simulation is more practical than it would be in a pool-first design.

The repo already has seams that make the narrow option straightforward:

- `packages/tracecat-registry/tracecat_registry/core/http.py` implements `core.http_request`
- `tracecat/agent/session/router.py` and `tracecat/agent/session/service.py` already carry per-session/per-run state into agent execution
- `tracecat/agent/types.py` and `tracecat/agent/workflow_config.py` already carry run config into the sandbox runtime
- `tracecat/executor/action_runner.py`, `tracecat/executor/minimal_runner.py`, and `tracecat/executor/backends/pool/worker.py` already create execution context for actions

The repo also has facts that shape the broad option:

- Many registry integrations call `httpx.AsyncClient()` directly rather than going through `core.http_request`
- Claude internet tools are explicitly special-cased in `tracecat/agent/runtime/claude_code/runtime.py`; those tools are not a normal local HTTP client path
- Action execution runs across multiple backend modes: direct subprocess, ephemeral nsjail subprocess, and warm pool workers
- For the current dev-mode problem, the relevant paths are `direct` and `ephemeral`

## Existing seams in this repo

### UI and session seam

Current agent testing already happens through session-backed chat surfaces:

- Preset testing and builder assistant live in `frontend/src/components/agents/agent-presets-builder.tsx`
- Shared streaming transport lives in `frontend/src/hooks/use-chat.ts`
- Session turn execution starts in `tracecat/agent/session/router.py` and `tracecat/agent/session/service.py`

This means a "dev mode" toggle can be modeled as either:

- org/app enablement plus a per-chat toggle
- a per-preset toggle
- a per-run flag sent with the chat request

### Action execution seam

Outbound action execution currently fans out across:

- `tracecat/executor/action_runner.py` for direct and ephemeral subprocess execution
- `tracecat/executor/minimal_runner.py` for in-process action execution inside warm workers
- `tracecat/executor/backends/pool/worker.py` for concurrent worker task handling

This is the important distinction:

- direct and ephemeral subprocesses can use environment variables safely per execution
- direct and ephemeral subprocesses also start fresh Python interpreters per execution, which makes startup hooks like `sitecustomize.py` and import-hook-based monkeypatching practical without patch leakage across runs
- warm workers cannot rely on process-global env mutation per task because tasks run concurrently
- warm workers need request-scoped configuration, likely through `ContextVar` or the request payload itself

For the current proposed product shape, the first bullet is the operationally important one. The warm-worker bullets are future compatibility notes, not current design drivers.

### Agent runtime seam

Agent runs already carry config into the sandbox runtime:

- `tracecat/agent/types.py` has `AgentConfig.enable_internet_access`
- `tracecat/agent/workflow_config.py` serializes agent config into workflow-safe payloads
- `tracecat/agent/executor/loopback.py` sends `RuntimeInitPayload`

That seam is useful for propagating simulation flags, but it does not by itself intercept outbound HTTP.

## Option 1: simulate `core.http_request` only

### What it is

When a run is in simulation mode, `core.http_request` returns a simulated response instead of making a real HTTP call.

### What it covers

- MCP-authored workflows that intentionally use `core.http_request`
- Templates that route through `core.http_request`
- Agent tool calls that execute `core.http_request`

### What it does not cover

- Registry integrations that instantiate `httpx.AsyncClient()` directly
- Other HTTP-capable actions unless they are retrofitted
- Anything outside the action/tool layer

Representative direct-`httpx` registry actions today:

- `packages/tracecat-registry/tracecat_registry/integrations/gmail.py` uses `httpx.AsyncClient()` to call Gmail REST endpoints directly for actions like listing and fetching messages
- `packages/tracecat-registry/tracecat_registry/integrations/google_drive.py` uses `httpx.AsyncClient()` directly against Drive APIs for file listing and related operations
- `packages/tracecat-registry/tracecat_registry/integrations/servicenow.py` issues direct `client.request(...)` calls with `httpx.AsyncClient()` for CRUD-style ServiceNow actions
- `packages/tracecat-registry/tracecat_registry/integrations/splunk.py` uses `httpx.AsyncClient()` directly for download and Splunk API operations such as KV collection ingest flows

Those are the clearest examples of why "`core.http_request` simulation" and "simulate outbound API traffic generally" are different scopes in this codebase.

### Why this option is attractive

- Smallest code change with immediate user value
- Clear UX: "this specific HTTP tool is simulated"
- Low blast radius
- Easy to test deterministically
- Can still be hosted behind a separate simulator service if desired

### Implementation variants

#### 1A. Inline matcher inside `core.http_request`

Add simulation lookup directly in `packages/tracecat-registry/tracecat_registry/core/http.py`.

Flow:

```text
chat/session toggle
  -> run/session config
  -> executor context
  -> core.http_request()
     -> if simulation enabled:
          match route
          build HTTPResponse
          return
     -> else:
          perform real httpx request
```

Sketch:

```python
async def http_request(...):
    sim = get_simulation_context()
    if sim.enabled:
        matched = await simulator.match(
            profile=sim.profile,
            request=NormalizedRequest(...)
        )
        if matched is not None:
            return matched.to_http_response()
        if sim.on_miss == "error":
            raise TracecatException("No simulated route matched")

    # existing real-network path
```

Pros:

- Fewest moving parts
- No extra service required
- Easiest first implementation

Cons:

- Hard-wires the first version to one action
- Future expansion to broad interception is mostly a separate project

#### 1B. `core.http_request` delegates to a simulator service

Keep the feature narrow in coverage, but push matching and response generation into a separate service.

Flow:

```text
core.http_request
  -> POST /simulate on simulator service
     -> simulator returns match / miss / passthrough decision
  -> if match: return simulated response
  -> if miss: error or real network based on policy
```

Suggested service contract:

```json
POST /simulate
{
  "profile_id": "optional",
  "inline_profile": {
    "routes": []
  },
  "request": {
    "method": "GET",
    "url": "https://api.example.com/v1/users/123",
    "headers": {"Authorization": "Bearer ..."},
    "query": {"limit": "10"},
    "json_body": null,
    "body_base64": null
  },
  "policy": {
    "on_miss": "error"
  }
}
```

Response:

```json
{
  "matched": true,
  "response": {
    "status_code": 200,
    "headers": {"Content-Type": "application/json"},
    "json_body": {"id": "123", "name": "Mock User"},
    "latency_ms": 120
  }
}
```

Pros:

- Keeps business logic out of the registry action
- Creates a reusable simulation engine for future proxy mode
- Can run as an internal service or external service

Cons:

- More moving parts than 1A
- Still only covers `core.http_request` unless other callers opt in

#### 1C. External function/service variant

Same as 1B, but the simulator can live outside Tracecat, including Modal or another hosted function.

Best fit:

- explicit `POST /simulate` matching API
- stateless or lightly stateful matching engine

Poor fit:

- transparent generic HTTP proxying
- long-lived proxy connections
- CONNECT tunneling
- streaming-heavy simulation

Reason: Modal-style functions are a good match for stateless request-in/response-out matching, but a poor match for acting as a general-purpose HTTP proxy with normal connection semantics.

### Recommended data model for Option 1

Even for a narrow first version, model the simulation profile as if it could later be reused elsewhere.

```ts
type SimulationMode = {
  enabled: boolean
  onMiss: "error" | "passthrough"
  profileId?: string
  inlineProfile?: SimulationProfile
}

type SimulationProfile = {
  name: string
  routes: SimulationRoute[]
}

type SimulationRoute = {
  id: string
  match: {
    method?: string
    host?: string
    path?: string
    pathPattern?: string
    query?: Record<string, string>
    headers?: Record<string, string>
    bodyJsonPath?: string
  }
  response: {
    statusCode: number
    headers?: Record<string, string>
    jsonBody?: unknown
    textBody?: string
    bodyBase64?: string
    latencyMs?: number
  }
}
```

The key choice is to separate:

- activation state for a run/session
- the simulation profile itself

That keeps the narrow version from painting the broad version into a corner.

### Evaluation

| Dimension | Assessment |
| --- | --- |
| Product value | High for MCP and workflow prototyping |
| Coverage | Narrow |
| Complexity | Low to medium |
| Infra cost | Low for 1A, medium for 1B/1C |
| Testability | Excellent |
| Risk | Low |
| Time to first useful version | Fast |

## Option 2: broad outbound HTTP simulation

### What it is

Intercept outbound HTTP for action execution more generally, so integrations and custom code can behave as if they are calling real APIs while actually hitting simulated responses.

### What "broad" really means here

In this repo, broad interception is mostly about action execution, not about every possible network path in the product.

It includes:

- `core.http_request`
- registry integrations using `httpx`
- other action-time HTTP usage inside executor backends

It does not automatically mean:

- Claude-hosted `WebSearch` or `WebFetch`
- arbitrary non-HTTP network protocols
- UI-side requests

### Important hard constraint

`tracecat/agent/runtime/claude_code/runtime.py` explicitly treats internet tools separately. Those are not the same thing as local `httpx` calls inside the executor. If the product goal is "simulate APIs while building workflows and actions", that should be framed as executor/action interception, not "all agent internet behavior."

### Implementation variants

#### 2A. Shared HTTP wrapper retrofit

Create a shared Tracecat HTTP helper and gradually move integrations onto it.

Flow:

```text
integration action
  -> tracecat_registry.net.request(...)
     -> if simulation enabled: use simulator
     -> else: real httpx
```

Sketch:

```python
async def request(req: NormalizedRequest) -> httpx.Response:
    sim = current_simulation()
    if sim.enabled:
        maybe = await simulator.match(req, sim)
        if maybe:
            return maybe.to_httpx_response()
    return await real_transport.send(req)
```

Pros:

- Cleanest long-term API surface
- Request-scoped config works naturally
- Easy to unit test

Cons:

- Only broad after a large retrofit
- Existing integrations still need to be migrated one by one

This is the clean architecture option, but not true broad interception on day one.

#### 2B. Context-aware `httpx` transport patch

Intercept `httpx` centrally by patching `httpx.AsyncClient` or swapping in a custom transport that consults request-scoped simulation context.

This is the only plausible "broad within Python code" option that works with warm pool workers without relying on process-global env.

Flow:

```text
worker request payload
  -> set ContextVar(simulation_context)
  -> patched httpx transport sees outgoing request
     -> simulator match
     -> simulate or passthrough
```

Sketch:

```python
simulation_ctx: ContextVar[SimulationMode | None]

class SimulatingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        sim = simulation_ctx.get()
        if sim and sim.enabled:
            matched = await simulator.match(request, sim)
            if matched:
                return matched.to_httpx_response(request)
        return await self._real.handle_async_request(request)
```

Pros:

- High coverage for Python HTTP traffic that uses `httpx`
- Compatible with warm worker concurrency if driven by `ContextVar`
- Can back onto an internal or external simulator service

Cons:

- More invasive and more brittle than Option 1
- Must ensure the patch is applied everywhere actions run
- Libraries not using `httpx` will bypass it
- Response fidelity has to match what callers expect from `httpx`

This is the most realistic broad strategy if "broad" means "most executor HTTP traffic."

#### 2C. HTTP(S)_PROXY to a dedicated proxy service

Set per-execution proxy config and let outbound clients talk through a simulator proxy.

Flow:

```text
executor run
  -> set HTTP_PROXY / HTTPS_PROXY
  -> action code uses normal http client
  -> proxy receives outbound request
     -> simulate or forward
```

Pros:

- Architecturally clean
- Decouples simulation engine from action code
- Works with multiple HTTP client libraries if they honor proxy env
- Natural fit for a separate internal service

Cons:

- Some future backends, especially warm pool, would need a different request-scoped config strategy
- Some libraries ignore proxy env unless configured explicitly
- CONNECT, TLS passthrough, and streaming edge cases increase complexity

For the current repo usage assumption, this option is a strong fit for draft/dev execution because direct and ephemeral subprocess execution can carry proxy configuration safely per run.

#### 2D. Lazy import-hook monkeypatching inside fresh subprocesses

Generate a `sitecustomize.py` per execution, load proxy/simulation config from env, and patch supported HTTP client libraries lazily when they are actually imported by the current UDF.

Flow:

```text
executor run
  -> write sitecustomize.py into job dir
  -> start fresh Python interpreter
  -> sitecustomize installs import hooks
  -> UDF imports requests/httpx/aiohttp/urllib
  -> module is patched on first import
  -> wrapper consults proxy/simulation config
  -> simulate, proxy, or passthrough
```

Backend applicability:

- `ephemeral`: natural fit because each action already gets an isolated job dir and a fresh interpreter
- `direct`: also required and practical, as long as the executor writes `sitecustomize.py` to a per-run directory and prepends that directory to `PYTHONPATH` before launching the subprocess
- `warm pool`: not a first-version target for this design, because worker startup is shared across concurrent tasks and needs request-scoped state rather than per-process startup state

Representative patch targets:

- `requests.Session.request`
- `httpx.Client.request`
- `httpx.AsyncClient.request`
- `aiohttp.ClientSession._request`
- `urllib.request.OpenerDirector.open`

Implementation notes:

- use env as the control plane for per-run configuration
- use wrapper functions as the behavior layer; do not rely on env-only behavior
- prefer wrapper functions over `functools.partial`, because the wrapper needs to evaluate scheme, `NO_PROXY`, and caller-provided overrides per request
- patch only modules actually imported by the current UDF
- make patchers idempotent and fail open in dev mode
- optionally also patch `core.http_request` and owned SDK clients as belt-and-suspenders coverage

Pros:

- More comprehensive than `core.http_request` for arbitrary Python code
- Better coverage than env-only proxy configuration because it can inject proxy kwargs even when clients do not honor env by default
- Low runtime overhead because only imported libraries are patched
- Strong fit for current `direct` and `ephemeral` execution because each action gets a fresh interpreter

Cons:

- Still not comprehensive: raw sockets, custom transports, and unpatched libraries bypass it
- More brittle than explicit wrappers owned by Tracecat
- Needs per-library regression tests and version-aware maintenance
- Much harder to make request-scoped in warm pool if that backend enters scope later

This is the strongest 80/20 application-layer option if the product goal is "capture most outbound HTTP from arbitrary Python in dev mode."

#### 2E. Transparent proxy / sidecar / network redirect

Route all outbound traffic through a proxy using network-level controls.

Examples:

- iptables redirect in the container/pod
- envoy-style egress proxy
- sidecar with egress capture

Pros:

- Potentially widest coverage
- Least change to application code

Cons:

- Operationally heavy
- Hard to make per-session or per-run
- Hard to reason about in local development
- Requires infra changes across docker-compose, Helm, Fargate, and EKS

This is the least attractive option for the current product goal.

#### 2F. External simulator service or Modal-hosted proxy

A separate service can host either:

- an explicit simulate API used by application code
- a true HTTP proxy

The explicit simulate API is realistic.
The true proxy is realistic only if the external service is long-lived and supports normal proxy semantics. A serverless function form factor is usually the wrong primitive for transparent proxying.

### Future constraint: warm pool workers

This is not the current blocker if dev executions stay on `direct` and `ephemeral`, but it is the main compatibility issue if broad interception ever needs to work on warm pool.

`tracecat/executor/backends/pool/worker.py` handles concurrent requests in one worker process, and `tracecat/executor/minimal_runner.py` uses `ContextVar` to isolate per-task registry context. That means:

- process-global `os.environ["HTTP_PROXY"] = ...` is unsafe
- per-task simulation state should be carried in the task request and stored in `ContextVar`
- broad interception in warm workers should prefer transport-level or wrapper-level request-scoped config
- process-global monkeypatch install-at-startup is only safe if the behavior still reads request-scoped state rather than a single global toggle

This is the core technical reason why "just use a proxy env var" is incomplete for warm pool, even though it is reasonable for current direct and ephemeral dev executions.

### Evaluation

| Dimension | Assessment |
| --- | --- |
| Product value | Highest if it works |
| Coverage | Medium to high, depending on implementation |
| Complexity | Medium to very high |
| Infra cost | Medium to high |
| Testability | Good for wrapper/transport, worse for transparent proxy |
| Risk | Medium to high |
| Time to first useful version | Slow unless heavily scoped |

## Shared product choices regardless of option

### Activation model

The cleanest product shape is:

- org/app-level enablement for safety
- per-chat or per-test-session toggle for actual use

Why:

- global always-on simulation is too easy to misuse
- per-preset persistence is useful later but is the wrong first activation model
- session-level activation matches how agent testing already works

### On-miss behavior

Supported behaviors should be:

- `error`
- `passthrough`

Default for dev mode should probably be `error`, because silent passthrough defeats the point of controlled simulation.

### Persistence

Best progression:

1. allow inline/ephemeral profiles attached to a test session
2. later add reusable workspace-level profiles

This keeps the first version lightweight without forcing a throwaway schema.

## Recommendation

### If the goal is time-to-value

Build Option 1 first, but shape it so the simulator engine can later sit behind a service boundary.

Most credible version:

- session-level activation in agent testing UI
- org/app-level enablement guard
- `core.http_request` interception
- simulator engine exposed as an internal service contract, even if initially implemented inline

That gives:

- immediate value for MCP and workflow authoring
- low delivery risk
- a future path toward broader interception

### If the goal is strategic coverage

If draft/dev executions are guaranteed to stay on `direct` and `ephemeral`, startup monkeypatching plus proxy/simulator config becomes a much more viable early broad solution. The first implementation should explicitly support both backends, not only `ephemeral`. If broad interception later needs to work on warm pool too, the implementation should evolve toward a request-scoped `httpx` transport or shared wrapper design.

Best strategic broad design:

1. define a simulator service contract
2. for current dev executions, pass proxy or simulator endpoint config via direct/ephemeral execution context
3. for current dev executions, inject `sitecustomize.py` and lazily patch common HTTP libraries on import for both `direct` and `ephemeral`
4. optionally set `HTTP_PROXY` / `HTTPS_PROXY` as a compatibility fallback for clients that already honor them
5. if warm pool ever enters scope, add request-scoped simulation config to executor payloads
6. implement a `httpx` transport or shared wrapper driven by `ContextVar` for warm pool compatibility
7. retrofit integrations over time where explicit control is still preferable

For the current operating assumption, lazy import-hook monkeypatching across both `direct` and `ephemeral` is the strongest 80/20 broad dev-mode solution. A pure env-proxy solution is simpler but weaker. The transport/wrapper path is the compatibility upgrade if executor usage changes later.

## Concrete sketches

### Shared run/session config sketch

```ts
type SimulationConfig = {
  enabled: boolean
  mode: "tool-only" | "broad-http"
  onMiss: "error" | "passthrough"
  profileId?: string
  inlineProfile?: SimulationProfile
}
```

Where it could flow:

```text
frontend toggle
  -> chat/session API
  -> AgentSession / run request
  -> agent workflow args or executor request
  -> runtime/action context
```

### Internal simulator service sketch

```text
client code
  -> normalize outbound request
  -> simulator service
     -> route match
     -> optional latency
     -> static or templated response
     -> miss policy
  -> simulated response or passthrough/error
```

### Broad transport sketch for future warm-pool compatibility

```text
pool worker request
  -> set ContextVar(simulation)
  -> patched httpx transport
     -> simulator.match(request)
     -> simulate or real network
  -> clear ContextVar
```

## Bottom line

`core.http_request` simulation is a focused product feature.
A general outbound interceptor or proxy is an execution-platform feature.

Both are valid, but they should be discussed and implemented as different products:

- Option 1 optimizes for immediate usefulness
- Option 2 optimizes for long-term realism and coverage

In this repo, the clean bridge between them is not "add more code to `core.http_request`." It is "define simulation config and a simulator service contract once, then choose whether callers opt in explicitly, patch common HTTP libraries in fresh subprocesses, or intercept HTTP more broadly below the app layer."
