# Session backend extensions

Tracecat owns session authorization, configuration resolution, approval validation,
stream rotation, and chat rendering. A trusted installed backend owns turn
dispatch, Temporal workflow identity and cancellation, and optionally native
history projection. Providers implement `SessionBackend` in `types.py`.

A separate distribution registers a stateless factory in its `pyproject.toml`:

```toml
[project.entry-points."tracecat.session_backends"]
v2 = "custom_backend.backend:create_backend"
```

The entry-point name is the persisted `backend_id`: an opaque, stable routing
identity, not a package or protocol version. `v1` is reserved for the built-in
provider (displayed as Standard). A separate provider can register `v2` and expose
an implementation-independent name such as Advanced. IDs must be lowercase
identifiers, at most 50 characters.

`harness_type` independently identifies the execution harness. Each backend
advertises `supported_harnesses` and `default_harness` internally. Creation
validates an explicit pair or resolves an omitted harness to that default. Both
identities are immutable once persisted. The discovery endpoint exposes backend
IDs and display names, not the supported harness list; ordinary chat creation
sends only `backend_id`. The built-in dispatcher passes the resolved harness
through to its workflow arguments.

The additive migration defaults existing rows and old-version inserts to `v1`
without rewriting harness/history data. Downgrade refuses to remove routing
identity while non-v1 sessions exist.

Factories are loaded once per API process. Duplicate names, malformed
registrations, and invalid providers fail startup. Installing a backend does not
replace any `tracecat.*` module. Providers are trusted application code, not
sandboxed extensions.

`is_enabled()` controls selection and discovery. The authenticated
`GET /workspaces/{workspace_id}/agent/sessions/backends` endpoint lists enabled
providers. The chat selector uses their display names. Unknown or disabled
identities reject execution; disabled installed providers still project history
and resolve lifecycle. Uninstalled providers leave metadata readable with
`backend_available=false`, `history_available=false`, and no projected messages;
an active turn's lifecycle is unavailable rather than fabricated as completed.
Restore the provider to read native history. The built-in provider is the default
for new sessions.

`start_turn()` receives resolved configuration and the execution role. Dispatch
must reserve the session before launching work. After committing ownership, a
lost dispatch acknowledgement must raise `SessionDispatchUncertain`: the HTTP
layer emits a terminal error but retains ownership so another send cannot race a
possibly running workflow. Definitive pre-dispatch validation failures use normal
validation exceptions.

Approval validation and idempotency remain in the shared session service. A
backend selects its Temporal update name and accepts `WorkflowApprovalSubmission`
with actor identity, decisions, overrides, metadata, and the rotated stream ID.
Cancellation is called only after authorization and live lifecycle validation.
Capabilities gate forks. Caller-owned workflow preparation currently supports
only `v1`; the durable workflow's session activity also rejects mismatched
backend/harness identities before changing any existing turn ownership. The
legacy workflow inbox and approval-history views explicitly limit themselves to
`v1`. Other backends use the shared session APIs for history and approvals.

A history adapter selects visible rows and projects each row into the existing
Claude-compatible chat display format (`ClaudeSDKMessageTA` and session-line
markers). This is an adapter to the current renderer, not a harness-neutral
display schema. It must preserve native records and IDs; display output must
never become native model history. Without an adapter, Tracecat uses the existing
Claude history path. A backend returning an adapter must implement both `load`
and `project`.

Backend packages should test against the exact Tracecat version used in their
image. This initial interface does not promise compatibility across arbitrary
Tracecat versions. The built-in workflow wire models retain their existing
fields and remain importable from the durable workflow module for existing code.
