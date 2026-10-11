import type { AgentOtelConfig } from "@/client"

/**
 * Claude Code OTel env vars Tracecat manages itself; users may not set them.
 * Mirrors `RESERVED` in tracecat/agent/otel_config.py.
 */
const RESERVED_ENV_VARS = new Set([
  "CLAUDE_CODE_ENABLE_TELEMETRY",
  "OTEL_EXPORTER_OTLP_HEADERS",
  "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
  "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
  "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
  "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",
  "OTEL_EXPORTER_OTLP_PROTOCOL",
  "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
  "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
  "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
])

interface OTelEnvSpec {
  key: string
  values?: readonly string[]
}

/**
 * Environment variables represented by the typed agent telemetry API. Every
 * key here has a dedicated form control; pasted env text maps onto them.
 */
const OTEL_ENV_SPECS: readonly OTelEnvSpec[] = [
  {
    key: "OTEL_METRICS_EXPORTER",
    values: ["otlp", "none"],
  },
  {
    key: "OTEL_LOGS_EXPORTER",
    values: ["otlp", "none"],
  },
  {
    key: "OTEL_TRACES_EXPORTER",
    values: ["otlp", "none"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_ENDPOINT",
  },
  {
    key: "OTEL_METRIC_EXPORT_INTERVAL",
  },
  {
    key: "OTEL_LOGS_EXPORT_INTERVAL",
  },
  {
    key: "OTEL_LOG_USER_PROMPTS",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_LOG_TOOL_DETAILS",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_LOG_TOOL_CONTENT",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_SESSION_ID",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_VERSION",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
    values: ["true", "false", "1", "0"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    values: ["cumulative", "delta"],
  },
  {
    key: "OTEL_RESOURCE_ATTRIBUTES",
  },
] as const

const OTEL_ENV_SPEC_BY_KEY: ReadonlyMap<string, OTelEnvSpec> = new Map(
  OTEL_ENV_SPECS.map((spec) => [spec.key, spec])
)

const POSITIVE_INTEGER_KEYS = new Set([
  "OTEL_METRIC_EXPORT_INTERVAL",
  "OTEL_LOGS_EXPORT_INTERVAL",
])

/** Enum keys the backend validates case-insensitively. */
const CASE_INSENSITIVE_ENUM_KEYS = new Set([
  "OTEL_METRICS_EXPORTER",
  "OTEL_LOGS_EXPORTER",
  "OTEL_TRACES_EXPORTER",
  "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
  "OTEL_LOG_USER_PROMPTS",
  "OTEL_LOG_TOOL_DETAILS",
  "OTEL_LOG_TOOL_CONTENT",
  "OTEL_METRICS_INCLUDE_SESSION_ID",
  "OTEL_METRICS_INCLUDE_VERSION",
  "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
])

const SIGNAL_EXPORTER_KEYS = [
  "OTEL_METRICS_EXPORTER",
  "OTEL_LOGS_EXPORTER",
  "OTEL_TRACES_EXPORTER",
] as const

/** Options controlling context-dependent environment validation. */
export interface EnvValidationOptions {
  /** Require an endpoint for each signal configured with the OTLP exporter. */
  requireOtlpEndpoint?: boolean
}

const ENDPOINT_KEY = "OTEL_EXPORTER_OTLP_ENDPOINT"
const METRIC_INTERVAL_KEY = "OTEL_METRIC_EXPORT_INTERVAL"
const LOGS_INTERVAL_KEY = "OTEL_LOGS_EXPORT_INTERVAL"
const TEMPORALITY_KEY = "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"
const RESOURCE_ATTRIBUTES_KEY = "OTEL_RESOURCE_ATTRIBUTES"

/**
 * Signal toggle key -> exporter env key. The relay supports only OTLP export,
 * so each typed toggle maps to either `otlp` or no raw value.
 */
const SIGNAL_KEYS = {
  traces: "OTEL_TRACES_EXPORTER",
  metrics: "OTEL_METRICS_EXPORTER",
  logs: "OTEL_LOGS_EXPORTER",
} as const

/**
 * Privacy/cardinality flag field -> env key. Each is tri-state: unset leaves
 * the backend default in place.
 */
const PRIVACY_FLAG_KEYS = {
  metricsIncludeSessionId: "OTEL_METRICS_INCLUDE_SESSION_ID",
  metricsIncludeVersion: "OTEL_METRICS_INCLUDE_VERSION",
  metricsIncludeAccountUuid: "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
  logUserPrompts: "OTEL_LOG_USER_PROMPTS",
  logToolDetails: "OTEL_LOG_TOOL_DETAILS",
  logToolContent: "OTEL_LOG_TOOL_CONTENT",
} as const

/** Which OTel signals are exported as `otlp`. */
export interface AgentOtelSignals {
  traces: boolean
  metrics: boolean
  logs: boolean
}

/** Metric temporality selection; empty string leaves the key unset. */
export type AgentOtelTemporality = "" | "delta" | "cumulative"

/** Explicit privacy and cardinality flags, keyed by form field name. */
export type AgentOtelPrivacyFlags = Record<
  keyof typeof PRIVACY_FLAG_KEYS,
  boolean
>

/** Runtime defaults per https://code.claude.com/docs/en/monitoring-usage. */
export const PRIVACY_FLAG_DEFAULTS: AgentOtelPrivacyFlags = {
  metricsIncludeSessionId: true,
  metricsIncludeVersion: false,
  metricsIncludeAccountUuid: true,
  logUserPrompts: false,
  logToolDetails: false,
  logToolContent: false,
}

/** Field name for each tri-state privacy flag. */
export type AgentOtelPrivacyFlagKey = keyof typeof PRIVACY_FLAG_KEYS

/** A resource attribute row in the key/value editor. */
export interface AgentOtelResourceAttribute {
  id: string
  name: string
  value: string
}

/**
 * Structured presentation of the flat OTel `env` map. Every allowlisted env key
 * maps onto a dedicated field. This env map is a presentation layer over the
 * typed API contract and is converted at the request boundary.
 */
export interface AgentOtelForm {
  /** OTEL_EXPORTER_OTLP_ENDPOINT value (empty string when unset). */
  endpoint: string
  /** OTEL_METRIC_EXPORT_INTERVAL value, kept as a string for the input. */
  metricIntervalMs: string
  /** OTEL_LOGS_EXPORT_INTERVAL value, kept as a string for the input. */
  logsIntervalMs: string
  /** Metrics temporality preference; empty leaves the key unset. */
  temporality: AgentOtelTemporality
  /** Per-signal `otlp` exporter toggles. */
  signals: AgentOtelSignals
  /** Privacy and cardinality flags, prefilled with the runtime defaults. */
  flags: AgentOtelPrivacyFlags
  /** OTEL_RESOURCE_ATTRIBUTES rows, serialized on submit. */
  resourceAttributes: AgentOtelResourceAttribute[]
}

function emptyFlags(): AgentOtelPrivacyFlags {
  return { ...PRIVACY_FLAG_DEFAULTS }
}

/** An empty form with every field unset and no signals exported. */
export function emptyAgentOtelForm(): AgentOtelForm {
  return {
    endpoint: "",
    metricIntervalMs: "",
    logsIntervalMs: "",
    temporality: "",
    signals: { traces: false, metrics: false, logs: false },
    flags: emptyFlags(),
    resourceAttributes: [],
  }
}

/** Generate a stable client-side id for a resource attribute row. */
export function newResourceAttributeRow(): AgentOtelResourceAttribute {
  return { id: crypto.randomUUID(), name: "", value: "" }
}

/** Parse a boolean env value, falling back to the flag's runtime default. */
function parseFlagValue(value: string | undefined, fallback: boolean): boolean {
  const normalized = value?.trim().toLowerCase()
  if (normalized === "true" || normalized === "1") {
    return true
  }
  if (normalized === "false" || normalized === "0") {
    return false
  }
  return fallback
}

/**
 * Split a flat OTel `env` map into the structured form shape. Unrecognized and
 * unparseable values fall back to unset. A signal toggle is ON iff its exporter
 * value is `otlp`.
 */
export function envMapToForm(env: Record<string, string>): AgentOtelForm {
  const form = emptyAgentOtelForm()

  form.endpoint = env[ENDPOINT_KEY] ?? ""
  form.metricIntervalMs = env[METRIC_INTERVAL_KEY] ?? ""
  form.logsIntervalMs = env[LOGS_INTERVAL_KEY] ?? ""

  const temporality = env[TEMPORALITY_KEY]?.trim().toLowerCase()
  if (temporality === "delta" || temporality === "cumulative") {
    form.temporality = temporality
  }

  for (const [signalName, exporterKey] of Object.entries(SIGNAL_KEYS)) {
    form.signals[signalName as keyof AgentOtelSignals] =
      env[exporterKey]?.trim().toLowerCase() === "otlp"
  }

  for (const [field, envKey] of Object.entries(PRIVACY_FLAG_KEYS)) {
    const key = field as AgentOtelPrivacyFlagKey
    form.flags[key] = parseFlagValue(env[envKey], PRIVACY_FLAG_DEFAULTS[key])
  }

  const rawAttributes = env[RESOURCE_ATTRIBUTES_KEY]
  if (rawAttributes !== undefined && rawAttributes.trim() !== "") {
    try {
      form.resourceAttributes = Object.entries(
        parseResourceAttributes(rawAttributes)
      ).map(([name, value]) => ({ id: crypto.randomUUID(), name, value }))
    } catch {
      // Malformed saved text still needs an editable surface; surface it as a
      // single row so validation reports the failure instead of dropping it.
      form.resourceAttributes = [
        { id: crypto.randomUUID(), name: rawAttributes, value: "" },
      ]
    }
  }

  return form
}

/**
 * Inverse of {@link envMapToForm}. Non-empty text fields are written trimmed;
 * empty ones are omitted entirely (no `KEY=`). Each signal toggle writes `otlp`
 * when on and omits the exporter key when off.
 */
export function formToEnvMap(form: AgentOtelForm): Record<string, string> {
  const env: Record<string, string> = Object.create(null)

  const endpoint = form.endpoint.trim()
  if (endpoint) {
    env[ENDPOINT_KEY] = endpoint
  }
  const metricInterval = form.metricIntervalMs.trim()
  if (metricInterval) {
    env[METRIC_INTERVAL_KEY] = metricInterval
  }
  const logsInterval = form.logsIntervalMs.trim()
  if (logsInterval) {
    env[LOGS_INTERVAL_KEY] = logsInterval
  }
  if (form.temporality) {
    env[TEMPORALITY_KEY] = form.temporality
  }

  for (const [signalName, exporterKey] of Object.entries(SIGNAL_KEYS)) {
    if (form.signals[signalName as keyof AgentOtelSignals]) {
      env[exporterKey] = "otlp"
    }
  }

  // Flags are always written explicitly so saved configs don't shift if the
  // runtime's defaults change.
  for (const [field, envKey] of Object.entries(PRIVACY_FLAG_KEYS)) {
    env[envKey] = String(form.flags[field as AgentOtelPrivacyFlagKey])
  }

  const attributes = resourceAttributeRowsToText(form.resourceAttributes)
  if (attributes) {
    env[RESOURCE_ATTRIBUTES_KEY] = attributes
  }

  return env
}

/**
 * Serialize resource attribute rows into the `OTEL_RESOURCE_ATTRIBUTES` value.
 * Blank rows are skipped; a row with only one side filled is kept so
 * validation reports it rather than silently dropping the edit.
 */
export function resourceAttributeRowsToText(
  rows: readonly AgentOtelResourceAttribute[]
): string {
  return rows
    .filter((row) => row.name.trim() !== "" || row.value.trim() !== "")
    .map(
      (row) =>
        `${encodeURIComponent(row.name.trim())}=${encodeURIComponent(row.value.trim())}`
    )
    .join(",")
}

function setOptionalBooleanEnv(
  env: Record<string, string>,
  key: string,
  value: boolean | null | undefined
): void {
  if (value !== null && value !== undefined) {
    env[key] = value ? "1" : "0"
  }
}

function parseOptionalBooleanEnv(
  env: Record<string, string>,
  key: string
): boolean | undefined {
  const value = env[key]?.trim().toLowerCase()
  if (value === undefined) {
    return undefined
  }
  return value === "true" || value === "1"
}

function serializeResourceAttributes(
  attributes: Record<string, string>
): string {
  return Object.entries(attributes)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(value)}`
    )
    .join(",")
}

/**
 * Resource attribute namespace Tracecat stamps per agent run, so org config
 * cannot shadow the identifiers that tie telemetry back to a session.
 */
export const RESERVED_ATTRIBUTE_PREFIX = "tracecat."

function parseResourceAttributes(value: string): Record<string, string> {
  // Null prototype so attribute names like `toString` or `__proto__` are
  // stored as own properties instead of colliding with Object.prototype.
  const attributes: Record<string, string> = Object.create(null)
  for (const item of value.split(",")) {
    const separator = item.indexOf("=")
    if (separator <= 0 || separator === item.length - 1) {
      throw new Error(
        "OTEL_RESOURCE_ATTRIBUTES must contain comma-separated key=value pairs."
      )
    }

    let key: string
    let attributeValue: string
    try {
      key = decodeURIComponent(item.slice(0, separator)).trim()
      attributeValue = decodeURIComponent(item.slice(separator + 1)).trim()
    } catch {
      throw new Error(
        "OTEL_RESOURCE_ATTRIBUTES contains invalid percent encoding."
      )
    }
    if (!key || !attributeValue) {
      throw new Error(
        "OTEL_RESOURCE_ATTRIBUTES names and values cannot be empty."
      )
    }
    if (Object.hasOwn(attributes, key)) {
      throw new Error(`OTEL_RESOURCE_ATTRIBUTES contains duplicate key ${key}.`)
    }
    attributes[key] = attributeValue
  }
  return attributes
}

/** Convert the typed API contract into the env-shaped UI presentation model. */
export function agentOtelConfigToEnvMap(
  config: AgentOtelConfig | undefined
): Record<string, string> {
  const env: Record<string, string> = {
    OTEL_METRICS_EXPORTER: (config?.metrics_enabled ?? true) ? "otlp" : "none",
    OTEL_LOGS_EXPORTER: (config?.logs_enabled ?? true) ? "otlp" : "none",
    OTEL_TRACES_EXPORTER: (config?.traces_enabled ?? false) ? "otlp" : "none",
  }

  if (config?.endpoint) {
    env.OTEL_EXPORTER_OTLP_ENDPOINT = config.endpoint
  }
  if (config?.metrics_temporality) {
    env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE =
      config.metrics_temporality
  }
  if (
    config?.metric_export_interval_ms !== null &&
    config?.metric_export_interval_ms !== undefined
  ) {
    env.OTEL_METRIC_EXPORT_INTERVAL = String(config.metric_export_interval_ms)
  }
  if (
    config?.logs_export_interval_ms !== null &&
    config?.logs_export_interval_ms !== undefined
  ) {
    env.OTEL_LOGS_EXPORT_INTERVAL = String(config.logs_export_interval_ms)
  }
  setOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_SESSION_ID",
    config?.metrics_include_session_id
  )
  setOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_VERSION",
    config?.metrics_include_version
  )
  setOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
    config?.metrics_include_account_uuid
  )
  setOptionalBooleanEnv(env, "OTEL_LOG_USER_PROMPTS", config?.log_user_prompts)
  setOptionalBooleanEnv(env, "OTEL_LOG_TOOL_DETAILS", config?.log_tool_details)
  setOptionalBooleanEnv(env, "OTEL_LOG_TOOL_CONTENT", config?.log_tool_content)
  if (
    config?.resource_attributes &&
    Object.keys(config.resource_attributes).length > 0
  ) {
    env.OTEL_RESOURCE_ATTRIBUTES = serializeResourceAttributes(
      config.resource_attributes
    )
  }
  return env
}

/** Convert the env-shaped UI presentation model into the typed API contract. */
export function envMapToAgentOtelConfig(
  enabled: boolean,
  env: Record<string, string>
): AgentOtelConfig {
  const config: AgentOtelConfig = {
    enabled,
    metrics_enabled: env.OTEL_METRICS_EXPORTER?.trim().toLowerCase() === "otlp",
    logs_enabled: env.OTEL_LOGS_EXPORTER?.trim().toLowerCase() === "otlp",
    traces_enabled: env.OTEL_TRACES_EXPORTER?.trim().toLowerCase() === "otlp",
  }

  const endpoint = env.OTEL_EXPORTER_OTLP_ENDPOINT?.trim()
  if (endpoint) {
    config.endpoint = endpoint
  }
  const temporality =
    env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE?.trim().toLowerCase()
  if (temporality === "delta" || temporality === "cumulative") {
    config.metrics_temporality = temporality
  }
  if (env.OTEL_METRIC_EXPORT_INTERVAL !== undefined) {
    config.metric_export_interval_ms = Number.parseInt(
      env.OTEL_METRIC_EXPORT_INTERVAL,
      10
    )
  }
  if (env.OTEL_LOGS_EXPORT_INTERVAL !== undefined) {
    config.logs_export_interval_ms = Number.parseInt(
      env.OTEL_LOGS_EXPORT_INTERVAL,
      10
    )
  }
  config.metrics_include_session_id = parseOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_SESSION_ID"
  )
  config.metrics_include_version = parseOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_VERSION"
  )
  config.metrics_include_account_uuid = parseOptionalBooleanEnv(
    env,
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID"
  )
  config.log_user_prompts = parseOptionalBooleanEnv(
    env,
    "OTEL_LOG_USER_PROMPTS"
  )
  config.log_tool_details = parseOptionalBooleanEnv(
    env,
    "OTEL_LOG_TOOL_DETAILS"
  )
  config.log_tool_content = parseOptionalBooleanEnv(
    env,
    "OTEL_LOG_TOOL_CONTENT"
  )
  if (env.OTEL_RESOURCE_ATTRIBUTES !== undefined) {
    config.resource_attributes = parseResourceAttributes(
      env.OTEL_RESOURCE_ATTRIBUTES
    )
  }
  return config
}

function envValueIssues(spec: OTelEnvSpec, value: string): string[] {
  const issues: string[] = []
  if (spec.values) {
    const allowed = new Set<string>(spec.values)
    const parts = [value]
    const normalizedParts = CASE_INSENSITIVE_ENUM_KEYS.has(spec.key)
      ? parts.map((part) => part.toLowerCase())
      : parts
    if (normalizedParts.some((part) => !allowed.has(part))) {
      issues.push(`${spec.key} supports ${spec.values.join(", ")}.`)
    }
  }
  if (POSITIVE_INTEGER_KEYS.has(spec.key)) {
    if (!/^[1-9]\d*$/.test(value)) {
      issues.push(`${spec.key} must be a positive integer.`)
    } else if (!Number.isSafeInteger(Number(value))) {
      // Number.parseInt would silently round (or overflow to Infinity,
      // serialized as null) before submission.
      issues.push(`${spec.key} must be at most ${Number.MAX_SAFE_INTEGER}.`)
    }
  }
  if (spec.key === ENDPOINT_KEY) {
    let endpoint: URL
    try {
      endpoint = new URL(value)
    } catch {
      issues.push(`${spec.key} must be an absolute HTTP(S) URL.`)
      return issues
    }
    if (
      (endpoint.protocol !== "http:" && endpoint.protocol !== "https:") ||
      !endpoint.hostname
    ) {
      issues.push(`${spec.key} must be an absolute HTTP(S) URL.`)
    }
    if (endpoint.username || endpoint.password) {
      issues.push(`${spec.key} must not include credentials.`)
    }
    // Check the raw string: URL.search/hash are "" for a bare "?" or "#",
    // after which the relay would append the OTLP path past the delimiter.
    if (value.includes("?")) {
      issues.push(`${spec.key} must not include a query string.`)
    }
    if (value.includes("#")) {
      issues.push(`${spec.key} must not include a fragment.`)
    }
  }
  if (spec.key === "OTEL_RESOURCE_ATTRIBUTES") {
    try {
      for (const key of Object.keys(parseResourceAttributes(value))) {
        if (key.startsWith(RESERVED_ATTRIBUTE_PREFIX)) {
          issues.push(
            `${key} is reserved; ${RESERVED_ATTRIBUTE_PREFIX}* attributes are ` +
              "set per agent run."
          )
        }
      }
    } catch (error) {
      issues.push(
        error instanceof Error
          ? error.message
          : "OTEL_RESOURCE_ATTRIBUTES is invalid."
      )
    }
  }
  return issues
}

/**
 * Validate an already-parsed `KEY -> value` env map against the same rules the
 * backend enforces: allowlist membership, reserved keys, per-key value rules
 * (enum/positive-int), and the OTLP-endpoint-required-when-exporter=otlp
 * cross-check. Returns human-readable messages; empty list means acceptable.
 *
 * This is the validator behind {@link validateForm}.
 */
export function validateEnvMap(
  env: Record<string, string>,
  { requireOtlpEndpoint = true }: EnvValidationOptions = {}
): string[] {
  const issues: string[] = []

  for (const [key, value] of Object.entries(env)) {
    if (RESERVED_ENV_VARS.has(key)) {
      issues.push(`${key} is managed by Tracecat.`)
      continue
    }
    const spec = OTEL_ENV_SPEC_BY_KEY.get(key)
    if (!spec) {
      issues.push(`${key} is not supported.`)
      continue
    }
    if (value.trim() === "") {
      issues.push(`${key} needs a value.`)
      continue
    }
    for (const message of envValueIssues(spec, value)) {
      issues.push(message)
    }
  }

  if (requireOtlpEndpoint) {
    const generic = env[ENDPOINT_KEY]
    for (const exporterKey of SIGNAL_EXPORTER_KEYS) {
      const value = env[exporterKey]
      if (value === undefined) continue
      if (value.trim().toLowerCase() === "otlp" && !generic) {
        issues.push(`${exporterKey}=otlp needs ${ENDPOINT_KEY}.`)
      }
    }
  }

  return issues
}

/**
 * Validate the structured form by materializing its merged env map via
 * {@link formToEnvMap} and running the shared {@link validateEnvMap} rules.
 * Returns human-readable messages; empty list means acceptable.
 */
export function validateForm(
  form: AgentOtelForm,
  { requireOtlpEndpoint = true }: EnvValidationOptions = {}
): string[] {
  // The env-key phrasing of the endpoint rule fits pasted text, not the form;
  // surface it here as a plain instruction instead.
  const issues = validateEnvMap(formToEnvMap(form), {
    requireOtlpEndpoint: false,
  })
  const signalOn =
    form.signals.traces || form.signals.metrics || form.signals.logs
  if (requireOtlpEndpoint && signalOn && form.endpoint.trim() === "") {
    issues.push("Add a collector endpoint to export the selected signals.")
  }
  return issues
}

/** A collector header entry before conversion to the API's header map. */
export interface AgentOtelHeaderEntry {
  name: string
  value: string
}

// RFC 7230 token: the only characters legal in an HTTP header name.
const HEADER_NAME_PATTERN = /^[!#$%&'*+\-.^_\x60|~0-9A-Za-z]+$/

// Control bytes make the exporter reject the request before it is sent.
// Control bytes and non-ASCII are unsendable: httpx ASCII-encodes header
// values, so the relay's request construction would fail client-side.
const HEADER_VALUE_INVALID_PATTERN = /[^\x20-\x7e]/

/** Validate collector header rows against what HTTP can actually send. */
export function validateAgentOtelHeaderEntries(
  entries: readonly AgentOtelHeaderEntry[]
): string[] {
  const seenNames = new Set<string>()
  for (const entry of entries) {
    const name = entry.name.trim()
    if (!name || !entry.value.trim()) {
      return ["Headers must map non-empty names to non-empty string values."]
    }
    if (!HEADER_NAME_PATTERN.test(name)) {
      return [
        `Header name ${name} is not a valid HTTP header name ` +
          "(letters, digits, and !#$%&'*+-.^_`|~).",
      ]
    }
    if (HEADER_VALUE_INVALID_PATTERN.test(entry.value)) {
      return [
        `Header ${name} value must contain only printable ASCII characters.`,
      ]
    }
    const normalizedName = name.toLowerCase()
    if (seenNames.has(normalizedName)) {
      return [`Header name ${name} is duplicated.`]
    }
    seenNames.add(normalizedName)
  }
  return []
}
