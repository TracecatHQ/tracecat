# Tracecat registry agent notes

Guidance for work under `packages/tracecat-registry/`, especially templates and integration wrappers.

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
