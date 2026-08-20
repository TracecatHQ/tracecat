# Tracecat registry agent notes

Guidance for work under `packages/tracecat-registry/`, especially templates and integration wrappers.

## Third-party integrations

Use these rules for new or materially expanded third-party integrations. Existing
integrations are compatibility references, not sources of truth for provider APIs;
do not break their public inputs or outputs without an explicitly planned migration.

### Research and implementation choice

- Deeply research the provider before authoring actions. Triangulate endpoint-specific
  official documentation, the official OpenAPI specification when one exists, and
  relevant official SDK or MCP schemas. Check authentication, scopes, API versions,
  request and response shapes, pagination, errors, and asynchronous states.
- Deep-link each action to its official endpoint documentation. A `doc_url` must
  point at the specific endpoint whenever a per-endpoint URL exists at all; a
  section index or documentation root is acceptable only after you have
  established that no per-endpoint address is reachable. If primary sources are
  incomplete or conflict, surface the gap during planning instead of guessing.
- Dig for the deep link before settling for a root URL. Modern vendor references
  are often single-page apps that look unlinkable but are not:
  - ReadMe, Redoc, Scalar, and Swagger UI derive per-operation URLs or fragments
    from the OpenAPI `operationId` or from `method + path`.
  - Postman documenters address every request and folder by UUID fragment. Read
    the published page's `href` for `/api/collections/<ownerId>/<publishedId>`,
    fetch that JSON, and map each action to its request `id`.
  - A `sitemap.xml`, `llms.txt`, or the OpenAPI document itself will often
    enumerate the addressable pages.
- Never invent a fragment or path you have not seen in the vendor's own data. A
  fabricated anchor silently resolves to the page root, so it looks correct in
  review and in CI while sending the reader nowhere. Prefer an honest root URL
  over a plausible guess, and say which actions took that fallback.
- Verify deep links against the vendor's own inventory, not HTTP status codes.
  Single-page docs return 200 for any path or fragment, so a status check proves
  nothing. Confirm each identifier appears in the sitemap, OpenAPI spec, or
  collection JSON you pulled it from.
- Strongly prefer YAML templates that call Tracecat's core HTTP actions for REST
  APIs. If research finds a maintained official Python SDK, ask the user during
  planning whether to use it. If approved, add generic direct and paginated SDK UDFs
  plus YAML endpoint templates over those wrappers.

### Thin-wrapper contract

- Keep one action close to one provider endpoint or SDK method. Use provider-native
  argument names and API-native `params` and `payload` shapes rather than recreating
  the provider's model, validation, or business logic.
- Do not add provider enums, duplicated argument validation, defensive state
  machines, or exception translation. Let HTTP status codes or native SDK exceptions
  remain authoritative.
- Narrow boundary handling is allowed for credential isolation and security,
  blocking private SDK dispatch, URL and path encoding, JSON serialization,
  binary or streaming values, protocol-required checks, and bounded pagination.
  Small input parsing or normalization is allowed only when it makes the outbound
  API call more robust; it must not become a semantic data transform.
- Declare credentials through `RegistrySecret` or OAuth rather than ordinary action
  inputs. Document required scopes, API versions, and product-tier constraints.
- Encode every dynamic URL path segment with `FN.url_encode`.
- Declare REST `base_url` as `str | None` with a `null` default. Resolve it in this
  order: `inputs.base_url || VARS.<tool_name>.base_url || <official public URL>`.
  Omit only the final fallback when the provider has no universal public endpoint.
- Return the untouched full `core.http_request` result, including `status_code`,
  `headers`, and `data`. Do not select, rename, filter, or reshape provider output.
  SDK wrappers may only adapt values enough to make the native result serializable.
- Pagination is the only general output-shaping exception. Follow the Slack and
  boto3 wrapper patterns: preserve provider order, document whether the result is a
  page list or flattened item list, and enforce the documented bound without
  otherwise transforming items.
- For polling, stop when the documented transient HTTP code or body state is no
  longer present. Do not test exact success equality or membership in a set of
  success values. Return the raw terminal response, including provider-declared
  failure states.

### Tests

- Do not add provider-specific white-box tests that mock an API or SDK and assert
  URLs, arguments, schemas, enums, payloads, or outputs. These restate the
  implementation without validating the real provider contract.
- Add live or sandbox provider tests only when a reliable environment exists and the
  user explicitly chooses that coverage during planning.
- Generic registry and template validation remains required. Narrow unit tests are
  allowed for Tracecat-owned platform security or protocol boundaries, such as
  credential isolation, preventing host filesystem or subprocess access, blocking
  ambient credential discovery, network-target restrictions, and shared protocol
  machinery. Provider-local dispatch, validation, pagination, or serialization is
  not a platform-boundary exception merely because Tracecat implements it.

## Template design

- Treat templates as thin API wrappers. Prefer passing through API-native shapes over reimplementing API validation or business logic in YAML/Python steps.
- Preserve existing input contracts. Add support additively; do not replace established shapes such as `list[dict[str, Any]]` with a different object contract unless explicitly requested.
- Avoid hard-coding provider enums or state machines when they can change upstream. Let the provider API validate mutable values such as statuses, transition IDs, priorities, field IDs, project-specific options, or vendor-specific enum strings.
- Avoid Python transform steps beyond small, mechanical payload assembly, such as collecting a list of field maps into one dict or preserving existing plaintext compatibility wrappers.
- Do not add defensive validation layers that catch provider errors and raise template-specific errors. Prefer sending the request and letting the provider API return the authoritative error.
- For object inputs, use simple API-native pass-through. Do not guess, recursively normalize, or validate arbitrary dictionaries unless the template contract explicitly defines that shape.
- If JSON-string parsing is intentionally supported for a field, keep it local and minimal. Do not add broad recursive parsing or magic conversions across generic payload maps.
- Keep narrow actions narrow. For example, a status-transition action should only transition; users should compose it with field-update or comment actions for extra writes.
- For field-map inputs, support rich objects by allowing them as values inside the existing field maps rather than changing the outer input shape.

## Jira ADF pattern

- Keep plain text compatibility where existing Jira templates already wrapped strings into ADF paragraphs.
- Accept ADF/native rich-text objects additively via `dict[str, Any]` on the specific rich-text field, or as values inside existing field maps.
- Do not add ADF-shape validators such as checking `version`, `type`, or `content`; Jira should validate ADF payload correctness.
