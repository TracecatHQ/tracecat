# Session backend extensions

Tracecat owns session authorization, configuration resolution, approval validation,
stream rotation, and chat rendering. A trusted installed backend owns turn
dispatch, Temporal workflow identity and cancellation, and optionally native
history projection. Providers implement `SessionBackend` in `types.py`.

A separate distribution registers a stateless factory in its `pyproject.toml`:

```toml
[project.entry-points."tracecat.session_backends"]
custom = "custom_backend.backend:create_backend"
```

The entry-point name is the persisted `harness_type`. It must be a lowercase
identifier, at most 50 characters. `claude_code` is reserved for the built-in
provider. Factories are loaded once per API process. Duplicate names, malformed
registrations, and invalid providers fail startup. Installing a backend does not
replace any `tracecat.*` module. Providers are trusted application code, not
sandboxed extensions.

`is_enabled()` controls selection and discovery. The authenticated
`GET /workspaces/{workspace_id}/agent/sessions/backends` endpoint lists enabled
providers. The chat selector uses that response. Unknown or disabled identities
are rejected; existing sessions cannot switch backend. The built-in provider is
the default for new sessions and for historical rows without a backend identity.

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
Capabilities explicitly gate forks and caller-owned workflow preparation.

A history adapter selects visible rows and projects each row into the existing
chat display format. It must preserve native records and IDs; display output must
never become native model history. Without an adapter, Tracecat uses the existing
Claude history path. A backend returning an adapter must implement both `load`
and `project`.

Backend packages should test against the exact Tracecat version used in their
image. This initial interface does not promise compatibility across arbitrary
Tracecat versions. The built-in workflow wire models retain their existing
fields and remain importable from the durable workflow module for existing code.
