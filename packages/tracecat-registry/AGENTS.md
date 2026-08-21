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
  point at the specific endpoint whenever a per-endpoint URL exists; a section
  index or documentation root is acceptable only after you have established that
  no per-endpoint address exists in the vendor's own inventory. If primary
  sources are incomplete or conflict, surface the gap during planning instead of
  guessing. See "Documentation links" below.
- Strongly prefer YAML templates that call Tracecat's core HTTP actions for REST
  APIs. If research finds a maintained official Python SDK, ask the user during
  planning whether to use it. If approved, add generic direct and paginated SDK UDFs
  plus YAML endpoint templates over those wrappers.

### Documentation links

- Dig for the deep link before settling for a root URL. Vendor references are
  often single-page apps that look unlinkable but are not:
  - ReadMe, Redoc, Scalar, and Swagger UI derive per-operation URLs or fragments
    from the OpenAPI `operationId` or from `method + path`.
  - Postman documenters address every request and folder by UUID fragment; the
    published page links its own collection JSON, which carries those ids.
  - A `sitemap.xml`, `llms.txt`, or the OpenAPI document usually enumerates the
    addressable pages.
- Confirm every identifier against the vendor's own inventory rather than an
  HTTP status. Single-page docs return 200 for any path or fragment, so a status
  check proves nothing, and an invented anchor silently resolves to the page
  root — looking correct in review and in CI while sending the reader nowhere.
  Never use a fragment or path you have not seen in the vendor's data; prefer an
  honest root URL and call out in the pull request which actions fall back to
  one.
- Prefer the current version of an endpoint. Check whether the vendor marks the
  one you picked `deprecated`, and if a non-deprecated successor exists with the
  same contract, build on that instead.

### Thin-wrapper contract

- Keep one action close to one provider endpoint or SDK method. Declare that endpoint's
  documented parameters as named action inputs, using the vendor's exact parameter names
  and the vendor's own wording for each description.
- Never expose a single generic `params`, `payload`, `body`, or `query_params` dictionary
  as a stand-in for an endpoint's parameters. An action's `expects` schema is handed
  directly to agents as their tool schema and rendered as the docs page, so an opaque
  dict makes the parameter surface unusable to both.
- "Do not recreate the provider's model" is about semantics, not naming. Naming the
  vendor's real parameters *is* the model. The rule forbids renaming or paraphrasing
  parameters, inventing parameters the vendor does not document, adding enums, regex or
  range validation, coercions, undocumented defaults, or business logic on top. It does
  not license erasing the parameter surface.
- Declare the parameters the action's use case needs — identifiers, filters, pagination,
  sorting, time ranges, output modifiers. You need not enumerate every documented
  parameter, but every parameter you do declare must be verbatim from the vendor's
  documentation.
- A free-form `dict[str, Any]` input is correct only where the vendor itself documents a
  free-form object: a JSON document body, a custom-fields map, a provider query DSL. It is
  never a substitute for named parameters. Most request bodies are not free-form — check
  the vendor's schema before assuming, because a flat documented field set is the common
  case and an opaque dict hides it.
- When you do keep a free-form `dict[str, Any]`, justify it with an inline YAML comment on
  the input saying which vendor construct makes it free-form. Without that note the next
  author cannot tell a deliberate exception from an unfinished one, and will copy it.
- **A request body whose optional fields cannot be sent as `null` is the one non-free-form
  case where a generic `payload` is still correct.** `core.http_request` prunes null
  `params` and `headers`, but not `payload`, because a JSON `null` in a body is often
  meaningful — GitHub documents `null` as "disable this protection" on branch protection.
  A literal `payload:` map therefore has no way to omit an unset optional: it emits
  `"comment": null`. Vendors that validate strictly reject that outright — GitHub answers
  `422 ... nil is not a boolean`, and Kibana's endpoint actions use zod `.optional()`,
  which fails on an explicit null.
- So before enumerating a body, check what the vendor does with a null optional. If it
  rejects them, keep `payload: dict[str, Any]` and **put the vendor's documented field list
  in the `description`** so the surface is still discoverable, plus an inline comment naming
  the reason. Enumerate a body only when every field is required, or the vendor accepts
  nulls on the optional ones — and say which, so the next author does not have to re-derive
  it.

```yaml
    payload:
      # Generic: Kibana validates this body with zod `.optional()`, which rejects an
      # explicit null, and a literal payload map cannot omit an unset optional.
      type: dict[str, Any]
      description: >-
        API-native request body. Documented fields for POST /api/endpoint/action/isolate:
        endpoint_ids (required), alert_ids, case_ids, comment, agent_type.
```

```yaml
    payload:
      # Free-form: OpenSearch query DSL. The body is the query language itself,
      # and its shape varies with the cluster's version.
      type: dict[str, Any]
      description: API-native OpenSearch search request body.
```
- Optional inputs are `<type> | None` with `default: null`, referenced directly from the
  literal `params:` or `headers:` map. `core.http_request` prunes null params and null
  headers, so an unset optional is omitted rather than sent as an empty value or rejected.
  Do not add a `core.script.run_python` step to strip nulls; the platform does it.
- Do not use `FN.merge` to assemble `params`, `payload`, or `headers`. Write one literal
  map. Merging makes precedence depend on argument order, and where a secret sits inside a
  merged map it lets a caller-supplied dict replace a declared credential.
- Put credentials where the vendor documents them, preferring a header when the vendor
  offers one. Put an API key in the query string only when that is the sole documented
  mechanism, and then place it directly in the literal `params:` map — not in the URL,
  because httpx replaces an existing query string when `params` is also passed.
- `core.http_request` types `headers` as `dict[str, str | None]`. String-valued
  expressions and nulls are allowed; a `bool` or `int` input fails validation before the
  request is made. Serialize booleans to the vendor's documented string values.
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
- Do not paginate inside an HTTP template. One action is one request. Expose the
  provider's own paging inputs — `page`, `page-size`, `limit`, `offset`, `cursor`,
  or whatever it calls them — return the single page the provider returned, and
  leave the loop to the workflow author, who drives it with a `while`/`until`
  loop over the cursor or offset. Do not call `core.http_paginate` from a
  template, and do not hide a fetch-all behind a `max_pages` argument.
  Auto-pagination hides cost and rate-limit pressure from the person running the
  workflow, and it hard-codes a stop condition that belongs to them.
- State the provider's documented maximum in the `description` of the relevant
  input when one exists, since callers cannot discover it from the schema and
  exceeding it is usually an error rather than a clamp.
- The SDK wrappers are the exception, not the precedent. Where a maintained SDK
  paginates natively — the Slack and boto3 wrappers — preserve provider order,
  document whether the result is a page list or a flattened item list, and
  enforce the documented bound without otherwise transforming items.
- For polling, stop when the documented transient HTTP code or body state is no
  longer present. Do not test exact success equality or membership in a set of
  success values. Return the raw terminal response, including provider-declared
  failure states.

#### Generic SDK dispatch wrappers

- The rules above govern action templates — the layer users and agents call. They do not
  govern a generic SDK dispatch UDF such as `tools.slack_sdk.call_method`, or the
  equivalent boto3, Google API, Cloudflare, Kubernetes and Okta wrappers. Those take an
  SDK method name plus a `params: dict[str, Any] | None` of that method's arguments, and
  being generic is their contract: the method is a runtime value, so there is no fixed
  parameter set to name.
- YAML templates layered over an SDK wrapper still follow the normal rules: declare the
  method's real named arguments as inputs and pass a literal `params:` map. See
  `templates/tools/slack/conversations/archive_channel.yml`, which declares `channel` and
  passes `sdk_method: conversations_archive` with `params: {channel: ${{ inputs.channel }}}`.
  A generic dict at the template layer is still wrong, SDK-backed or not.
- Null-pruning is a `core.http_request` behaviour and does not reach SDK-backed templates.
  SDK wrappers follow the SDK's own semantics — `slack_sdk.call_method` forwards `params`
  as kwargs untouched, while `okta_sdk` prunes explicitly via its own `_drop_none`. When
  adding an SDK wrapper, state which it does.

#### Self-hosted APIs on a customer-controlled version

- A generic `params: dict[str, Any] | None` is the correct shape when `base_url` resolves to
  infrastructure the customer runs and whose version we cannot know. Elasticsearch and OpenSearch are
  the cases in this registry: neither pins an API version, clusters in the field span Elasticsearch
  7/8/9, and OpenSearch has been a hard fork with diverging parameters since 2021. A named parameter
  set baked into the template would be wrong for some fraction of clusters with no way to detect it.
- This is about version control, not parameter count. Endpoint size is not the test: several of those
  endpoints document only five query parameters, while enumerated providers elsewhere in this registry
  declare 27 and 28 on a single action.
- The test is whether the template pins the API version. `elastic_security` targets Kibana at a
  pinned `/v8/` with `kbn-xsrf`, so its surface is fixed and its parameters are named — do not group
  it with `elasticsearch` on the strength of the name. `github` (`X-GitHub-Api-Version`), `gitlab`
  (`/api/v4`) and `sublime` (`/v0/`) are likewise versioned and enumerated.
- The request body is a separate question. An Elasticsearch or OpenSearch search body is a query DSL
  and stays `dict[str, Any]` under the free-form rule above, regardless of how the query parameters
  are modelled.

#### Unset optional parameters

- `core.http_request`, `core.http_poll` and `core.http_paginate` drop `None` values from
  `params` and `headers` before the request is built. Only `None` is dropped: `""`,
  `false` and `0` are sent, because some APIs use an empty value as a presence flag.
- This was not always true. Unset optional query parameters were previously serialized as
  `key=` (httpx maps `None` to `""`), and unset optional headers were rejected outright by
  argument validation. Templates worked around it with `core.script.run_python` params
  builders or `FN.merge`. Neither is needed any more; do not reintroduce them.
- Pruning applies to `params` and `headers` only. `payload` and `form_data` are **not**
  pruned, because a JSON `null` is a meaningful value in a request body and the vendor —
  not Tracecat — decides what it means. When an endpoint's optional body fields cannot be
  sent as `null`, say so in the action description and let the caller omit them; do not add
  a step to strip them.

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
