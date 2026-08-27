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

These rules govern **new and materially expanded** integrations, and the registry
predates them. Where a rule governs a **published contract** — an action's `returns`,
its declared inputs, or how it takes credentials — existing templates keep theirs:
521 of 880 return `.result.data`, 238 declare `base_url` differently, and `jira`,
`confluence` and `opensearch` authenticate over Basic. Conform new work; migrate
existing work only on explicit request, never as a drive-by. Every other rule here
is unconditional — those describe internal implementation, not contracts, so fixing
a violation breaks nothing and no exemption should be inferred.

#### Naming the parameters

- Keep one action close to one provider endpoint or SDK method. Declare that endpoint's
  documented parameters as named action inputs, using the vendor's exact parameter names
  and the vendor's own wording for each description.
  Example: `tools/hunter/search/domain_search.yml`.
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
- Optional inputs are `<type> | None` with `default: null`, referenced directly from the
  literal `params:` or `headers:` map. See "Unset optional parameters" for what the
  platform does with them.
- Do not use `FN.merge` to assemble `params`, `payload`, or `headers`. Write one literal
  map. Merging makes precedence depend on argument order, and where a secret sits inside a
  merged map it lets a caller-supplied dict replace a declared credential.

#### Choosing the input shape

Named inputs are the default. A generic dictionary is correct only in these cases, and
each one must carry an inline YAML comment saying which applies — without it the next
author cannot tell a deliberate exception from an unfinished one, and will copy it.

| Situation | Shape | Why | Example |
|---|---|---|---|
| The endpoint documents a parameter set | named inputs | the default | `tools/hunter/search/domain_search.yml` |
| The vendor documents a free-form object | `dict[str, Any]` | query DSL, custom-field map, user-supplied document | `tools/gitlab/security/create_project_vulnerability_export_and_wait.yml` (`report_data`) |
| The vendor rejects `null` on optional body fields | `payload: dict[str, Any]` | a literal map cannot omit a field | `tools/elastic_security/endpoint_response/isolate_endpoint.yml` |
| The vendor's body is a discriminated union | `payload: dict[str, Any]` | there is no single field set to name | `tools/elastic_security/detections/create_detection_rule.yml` |
| `base_url` resolves to a customer-run cluster | `params: dict[str, Any]` | the API version is unknowable | `tools/opensearch/search_events.yml` |
| A generic SDK dispatch UDF | `params: dict[str, Any]` | the method is a runtime value | `integrations/slack_sdk.py` |

Most request bodies are **not** free-form — check the vendor's schema first, because a flat
documented field set is the common case. Before enumerating a body, check what the vendor does with a
null optional: `payload` is not null-pruned (see "Unset optional parameters"), so a literal map emits
`"comment": null`, which strict validators reject — GitHub answers `422 ... nil is not a boolean`,
Kibana's endpoint actions use zod `.optional()`. Where that is so, keep the dict and put the vendor's
documented field list in the `description` so the surface stays discoverable.

```yaml
    payload:
      # Generic: Kibana validates this body with zod `.optional()`, which rejects an
      # explicit null, and a literal payload map cannot omit an unset optional.
      type: dict[str, Any]
      description: >-
        API-native request body. Documented fields for POST /api/endpoint/action/isolate:
        endpoint_ids (required), alert_ids, case_ids, comment, agent_type.
```

The self-hosted case is about version control, not parameter count — several of those endpoints
document only five query parameters, while enumerated providers here declare 27 and 28 on a single
action. The test is whether the template pins the API version: `elastic_security` targets Kibana at a
pinned `/v8/`, so its parameters are named — do not group it with `elasticsearch` on the strength of
the name. A cluster's *body* is separate: a search body is a query DSL and stays `dict[str, Any]`
regardless. Templates over an SDK dispatch wrapper follow the normal rules — see
`tools/slack/conversations/archive_channel.yml`, which declares `channel` and passes
`params: {channel: ${{ inputs.channel }}}`. A generic dict at the *template* layer is still wrong,
SDK-backed or not.

#### Input names, wire keys, and types

- Where the vendor's parameter name is not a valid Python identifier, the input takes a safe name
  and the wire key keeps the vendor's exact string: `tweet_fields` → `"tweet.fields"`
  (`tools/x/posts/get_tweet.yml`), `epss_gt` → `epss-gt`
  (`tools/first_epss/scores/search_scores.yml`), `iids` → `"iids[]"`
  (`tools/gitlab/merge_requests/list_merge_requests.yml`).
- **Do not rename when the vendor's name already works.** Python keywords are fine as input names —
  `inputs.in`, `inputs.from` and `inputs.not` all parse and resolve — and so is unusual casing, as
  with Rippling's `Operations`. An alias you did not need is an invented parameter.
- Array query parameters depend on the vendor's serialization, and getting it wrong is silent.
  httpx emits a list as repeated pairs (`a=1&a=2`), which is OpenAPI `explode: true` and the right
  form for most backends. Two exceptions: `explode: false` means one comma-separated value, so type
  the input `str` (`tools/socket/packages/fetch_packages_by_purl.yml`, `actions` and `labels`); and
  Rack-backed GitLab collapses repeated bare keys to the last value, so it uses `[]` suffixes and
  comma-separated coercers. Check the spec's `style`/`explode`, not just the documented type.
- The type grammar supports more than the primitives: `list[int]`, `list[str]`,
  `list[dict[str, Any]]`, `dict[str, Any]`, `datetime`, and unions with `None`. Reach for the
  precise type rather than widening to `str` or dropping a field as inexpressible.
- Do not add provider enums. Put the vendor's allowed values in the `description` instead; enums
  change upstream and the API stays authoritative.
- State the provider's documented maximum in the `description` of the relevant
  input when one exists, since callers cannot discover it from the schema and
  exceeding it is usually an error rather than a clamp.

#### Credentials

- Treat connection configuration separately from authentication material. Base
  URLs, workspace hosts, account URLs, regions, and similar routing values are
  not secrets. Expose them as a `base_url`/host action input and resolve them
  through `inputs.<name> || VARS.<tool_name>.<name> || <official default>` where
  a default exists. Never put a non-sensitive base URL or host in
  `RegistrySecret`; this applies to SDK wrappers as well as HTTP templates. A
  template over an SDK wrapper declares the optional input and passes the
  resolved value into a required wrapper argument; the wrapper must not recover
  routing configuration from a secret or ambient environment variable.
- Distinguish multiple placements of one credential from genuinely different
  authentication modes. If the same API key can be sent in a query parameter,
  custom header, Bearer header, or Basic auth, implement one safest documented
  header placement rather than duplicating it. Hunter documents an `api_key`
  query parameter, an `X-API-KEY` header, and a Bearer header; the template sends
  `X-API-KEY` only. Socket documents Bearer and Basic; the template sends Bearer
  only. This rule does not decide whether a provider should expose API-key auth,
  OAuth, or both.
- Put an API key in the query string only when that is the sole documented mechanism, and then
  place it directly in the literal `params:` map — not in the URL, because httpx replaces an
  existing query string when `params` is also passed. Example: `tools/shodan/hosts/lookup_host.yml`.
- Use Basic when Basic *is* the vendor's mechanism — Atlassian's API tokens are email + token over
  Basic, so `jira` and `confluence` are correct. `core.http_request`'s `auth:` argument and an
  explicit `Authorization: Basic ${{ FN.to_base64(...) }}` header are equally fine.
  **Shipped providers that authenticate over Basic keep it**: `jira`, `confluence` and `opensearch`
  do, the vendors still accept it, and a workspace's stored secret is a published contract. Add a
  header-token path alongside if one exists, but never replace. Existing templates only — this does
  not license Basic in new work.
- Choose supported authentication modes case by case from the provider's current
  production guidance, security posture, and the mechanisms users ordinarily
  deploy. Do not add every mechanism merely because the API documents it, and do
  not remove a commonly used API-key path merely because OAuth also exists.
  Jamf and similar integrations should retain API-key support when that remains
  the normal user path. Databricks and Snowflake should use OAuth-only flows when
  OAuth is the production norm. When both modes are materially used, support both;
  `jamf/computers/*` and `google_scc/*` show the combined-secret pattern. Record
  the choice and evidence in the implementation plan or PR.
- Declare credentials through `RegistrySecret` or OAuth rather than ordinary action
  inputs. Document required scopes, API versions, and product-tier constraints.
- When an integration is intentionally OAuth-only, do not add PAT, API-token,
  raw bearer-token, ambient credential-discovery, or undocumented compatibility
  paths. Implement only the approved OAuth grants.
- For integrations that support user OAuth, service OAuth, and an
  environment-scoped stored credential fallback, use this precedence:
  authorization-code token, client-credentials token, then stored credentials.
  Keep each OAuth secret optional so either grant can be configured independently.
  Every OAuth path must validate and run without requiring the stored credential.
  The stored secret may also be optional: `optional=True` controls whether the
  secret group must exist, not whether fields inside a configured group are
  required. Put its minimum usable fields in `keys` and only genuine modifiers in
  `optional_keys`; never model an optional secret group by moving its required
  fields into `optional_keys`. Follow `google_api_optional_secret` as the schema
  pattern.
- Do not duplicate OAuth exchange logic unless the standard provider cannot
  represent a required vendor flow. Verify the grant, endpoint authentication
  method, scopes, PKCE requirements, refresh behavior, and any audience/resource
  parameters against current official documentation. If a generic service
  provider cannot express provider-specific token parameters, state that limit
  and keep those parameters in the environment-scoped credential fallback.
- `core.http_request` types `headers` as `dict[str, str | None]`. String-valued expressions and
  nulls are allowed; a `bool` or `int` input fails validation before the request is made. Serialize
  booleans to the vendor's documented string values.

#### Inputs, outputs, and transforms

- **New actions return the untouched full `core.http_request` result**, including `status_code`,
  `headers`, and `data`. The envelope is not decoration: pagination cursors and `Link` headers live
  in `headers`, and an action returning only `.data` cannot be paged by the workflow author. Do not
  select, rename, filter, or reshape provider output. SDK wrappers may only adapt values enough to
  make the native result serializable. Example: `tools/shodan/hosts/lookup_host.yml`.
  **Do not change a shipped action's `returns`** — 521 of 880 templates return `.result.data`, and
  that is a published output contract.
- **New actions declare REST `base_url` as `str | None` with a `null` default**, resolved
  `inputs.base_url || VARS.<tool_name>.base_url || <official public URL>`. Omit only the final
  fallback when the provider has no universal public endpoint. Existing templates that declare it
  differently keep their contract.
- Most actions need no transform at all — 75% of templates pass the request straight through, and
  that is the target. Do not reshape provider input or output to make an action feel tidier.
- When a transform is genuinely required, use a `core.script.run_python` step. Prefer it over a
  chain of inline `FN.*` expressions and `core.transform.reshape`: a named function with real
  control flow is easier to read and review than an expression pipeline, and it keeps the logic in
  one place. Example: `tools/elastic_security/endpoint_response/isolate_endpoint.yml` builds the
  Kibana space prefix in a `build_path` step.
- Choosing Python is about *how* to write a necessary transform, not permission to add unnecessary
  ones. Keep narrow actions narrow: a status-transition action should only transition. Do not add
  duplicated argument validation, defensive state machines, or exception translation — send the
  request and let the provider return the authoritative error.
- Narrow boundary handling is allowed for credential isolation and security,
  blocking private SDK dispatch, URL and path encoding, JSON serialization,
  binary or streaming values, protocol-required checks, and bounded pagination.
  Small input parsing or normalization is allowed only when it makes the outbound
  API call more robust; it must not become a semantic data transform.
- If JSON-string parsing is intentionally supported for a field, keep it local and minimal. Do not
  add broad recursive parsing or magic conversions across generic payload maps.
- For field-map inputs, support rich objects by allowing them as values inside the existing field
  maps rather than changing the outer input shape.

#### SDK wrapper mechanics

- Use an established generic SDK wrapper, especially `slack_sdk.py`, as the
  structural reference before inventing a new dispatch pattern. Preserve simple
  Python idioms from that reference, including an assignment expression such as
  `if method := getattr(client, sdk_method, None):` when resolving an optional
  callable.
- Keep API semantics in the official SDK or provider. Wrapper-side validation is
  limited to Tracecat-owned safety and protocol boundaries such as blocking
  private dispatch, preventing credential exfiltration, bounding pagination, and
  producing JSON-serializable results. Do not duplicate the provider's request
  validation, enums, required-field checks, or error interpretation.
- Separate direct and paginated dispatch explicitly. A direct dispatcher must
  never return an `Iterator` or generator unchanged; reject it with a clear
  instruction to use the paginated action. The paginated dispatcher must collect
  only the caller-requested bounded number of items.
- Inspect the exact pinned SDK implementation and runtime type before adapting
  special values such as waiters, pagers, futures, and response wrappers. Do not
  infer what `bind()`, `result()`, or a similarly named method returns. Serialize
  the real public response and binding data without recursively serializing the
  wrapper object itself.
- Before declaring an SDK template complete, confirm against the pinned SDK that
  every referenced `service.method` exists and every forwarded keyword is
  accepted by that method's signature.

#### Pagination and polling

- Do not paginate inside an HTTP template. One action is one request. Expose the
  provider's own paging inputs — `page`, `page-size`, `limit`, `offset`, `cursor`,
  or whatever it calls them — return the single page the provider returned, and
  leave the loop to the workflow author, who drives it with a `while`/`until`
  loop over the cursor or offset. Do not call `core.http_paginate` from a
  template, and do not hide a fetch-all behind a `max_pages` argument.
  Auto-pagination hides cost and rate-limit pressure from the person running the
  workflow, and it hard-codes a stop condition that belongs to them.
- The SDK wrappers are the exception, not the precedent. Where a maintained SDK
  paginates natively — the Slack and boto3 wrappers — preserve provider order,
  document whether the result is a page list or a flattened item list, and
  enforce the documented bound without otherwise transforming items.
- For polling, stop when the documented transient HTTP code or body state is no
  longer present. Do not test exact success equality or membership in a set of
  success values. Return the raw terminal response, including provider-declared
  failure states.

#### Unset optional parameters

- `core.http_request`, `core.http_poll` and `core.http_paginate` drop `None` values from
  `params` and `headers` before the request is built. Only `None` is dropped: `""`,
  `false` and `0` are sent, because some APIs use an empty value as a presence flag.
- This was not always true. Unset optional query parameters were previously serialized as
  `key=` (httpx maps `None` to `""`), and unset optional headers were rejected outright by
  argument validation. Templates worked around it with `core.script.run_python` params
  builders or `FN.merge`. Neither is needed any more; do not reintroduce them for this purpose.
- Pruning applies to `params` and `headers` only. `payload` and `form_data` are **not**
  pruned, because a JSON `null` is a meaningful value in a request body and the vendor —
  not Tracecat — decides what it means. When an endpoint's optional body fields cannot be
  sent as `null`, keep a generic `payload` and say so in the description; do not add a step
  to strip them.
- Null-pruning is a `core.http_request` behaviour and does not reach SDK-backed templates. SDK
  wrappers follow the SDK's own semantics — `slack_sdk.call_method` forwards `params` as kwargs
  untouched, while `okta_sdk` prunes explicitly via its own `_drop_none`. When adding an SDK
  wrapper, state which it does.

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
  machinery. Generic SDK wrapper dispatch, bounded pagination, waiter handling,
  and JSON serialization are wrapper-owned protocol boundaries: when fixing one,
  add a narrow regression test using the pinned SDK's real runtime type or contract
  rather than a mock with assumed behavior. Provider request validation is not a
  platform-boundary exception merely because Tracecat implements it.

### Integration assets

- Add the provider's official vector logo when introducing an integration or
  OAuth provider. Prefer the vendor's current SVG asset over a raster image,
  unofficial redraw, wordmark screenshot, or generated approximation.
- Verify the committed SVG at rendered integration-icon size on both light and
  dark backgrounds. Preserve official brand colors, transparency, and aspect
  ratio; add a deliberate light/dark variant only when the official mark is not
  legible in both modes.

### Integration review checklist

Before handing off an integration PR, explicitly re-audit:

- the full action/template inventory and each exact endpoint or SDK method;
- action inputs and `VARS`, especially that no base URL or host leaked into a secret;
- credential schemas, OAuth grant registrations, scopes, endpoints, and runtime
  precedence, including the environment-scoped fallback;
- each authentication path independently, confirming an OAuth-only configuration
  has no hidden dependency on a stored basic secret and an optional stored secret
  still requires all fields needed to authenticate when it is configured;
- absence of unapproved PAT, API-token, raw-token, and ambient-auth paths;
- JSON serialization for ordinary responses, waiters, and paginated iterators;
- official SVG mappings for action namespaces, credentials, and OAuth providers;
- focused registry/provider tests and all current unresolved PR review threads.

## Jira ADF pattern

- Keep plain text compatibility where existing Jira templates already wrapped strings into ADF paragraphs.
- Accept ADF/native rich-text objects additively via `dict[str, Any]` on the specific rich-text field, or as values inside existing field maps.
- Do not add ADF-shape validators such as checking `version`, `type`, or `content`; Jira should validate ADF payload correctness.
