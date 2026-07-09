import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import type { Extension } from "@codemirror/state"
import type { EditorView } from "@codemirror/view"
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
 * Environment variables represented by the typed Agent OTel API. Raw mode is
 * an alternate editor for these fields, not an escape hatch around the API.
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

/** A validation issue tied to a 1-indexed line in the env editor. */
export interface EnvIssue {
  lineNumber: number
  message: string
}

/** Options controlling context-dependent environment validation. */
export interface EnvValidationOptions {
  /** Require an endpoint for each signal configured with the OTLP exporter. */
  requireOtlpEndpoint?: boolean
}

/**
 * First-class OTel env keys surfaced as dedicated form fields. Every other
 * allowlisted key stays reachable through the Advanced env editor.
 */
const FIRST_CLASS_ENDPOINT_KEY = "OTEL_EXPORTER_OTLP_ENDPOINT"
const FIRST_CLASS_METRIC_INTERVAL_KEY = "OTEL_METRIC_EXPORT_INTERVAL"

/**
 * Signal toggle key -> exporter env key. The relay supports only OTLP export,
 * so each typed toggle maps to either `otlp` or no raw value.
 */
const FIRST_CLASS_SIGNAL_KEYS = {
  traces: "OTEL_TRACES_EXPORTER",
  metrics: "OTEL_METRICS_EXPORTER",
  logs: "OTEL_LOGS_EXPORTER",
} as const

/** The set of env keys owned by first-class form fields. */
const FIRST_CLASS_KEYS: ReadonlySet<string> = new Set<string>([
  FIRST_CLASS_ENDPOINT_KEY,
  FIRST_CLASS_METRIC_INTERVAL_KEY,
  ...Object.values(FIRST_CLASS_SIGNAL_KEYS),
])

/** Which OTel signals are exported as `otlp`. */
export interface AgentOtelSignals {
  traces: boolean
  metrics: boolean
  logs: boolean
}

/**
 * Structured presentation of the flat OTel `env` map. First-class fields are
 * pulled out into dedicated inputs; everything else lives in `advancedEnv` as
 * raw `KEY=value` text. This env map is a presentation layer over the typed API
 * contract and is converted at the request boundary.
 */
export interface AgentOtelForm {
  /** OTEL_EXPORTER_OTLP_ENDPOINT value (empty string when unset). */
  endpoint: string
  /** OTEL_METRIC_EXPORT_INTERVAL value, kept as a string for the input. */
  metricIntervalMs: string
  /** Per-signal `otlp` exporter toggles. */
  signals: AgentOtelSignals
  /** Raw `KEY=value` text for all non-first-class env keys. */
  advancedEnv: string
}

/**
 * Split a flat OTel `env` map into the structured form shape. First-class keys
 * become dedicated fields; the remaining keys are serialized into
 * `advancedEnv` via {@link envMapToText}. A signal toggle is ON iff its
 * exporter value is `otlp`.
 */
export function envMapToForm(env: Record<string, string>): AgentOtelForm {
  const advanced: Record<string, string> = {}
  const signals: AgentOtelSignals = {
    traces: false,
    metrics: false,
    logs: false,
  }

  for (const [key, value] of Object.entries(env)) {
    const signalEntry = Object.entries(FIRST_CLASS_SIGNAL_KEYS).find(
      ([, exporterKey]) => exporterKey === key
    )
    if (signalEntry) {
      const signalName = signalEntry[0] as keyof AgentOtelSignals
      signals[signalName] = value.trim().toLowerCase() === "otlp"
      continue
    }
    if (!FIRST_CLASS_KEYS.has(key)) {
      advanced[key] = value
    }
  }

  return {
    endpoint: env[FIRST_CLASS_ENDPOINT_KEY] ?? "",
    metricIntervalMs: env[FIRST_CLASS_METRIC_INTERVAL_KEY] ?? "",
    signals,
    advancedEnv: envMapToText(advanced),
  }
}

/**
 * Inverse of {@link envMapToForm}. Starts from the parsed `advancedEnv` tail
 * and overlays the first-class fields on top, so first-class fields win on key
 * collision. Non-empty first-class text fields are written trimmed; empty ones
 * are omitted entirely (no `KEY=`). Each signal toggle writes `otlp` when on
 * and removes the exporter key when off.
 */
export function formToEnvMap(form: AgentOtelForm): Record<string, string> {
  const env: Record<string, string> = parseEnvText(form.advancedEnv)

  const endpoint = form.endpoint.trim()
  if (endpoint) {
    env[FIRST_CLASS_ENDPOINT_KEY] = endpoint
  } else {
    delete env[FIRST_CLASS_ENDPOINT_KEY]
  }

  const metricInterval = form.metricIntervalMs.trim()
  if (metricInterval) {
    env[FIRST_CLASS_METRIC_INTERVAL_KEY] = metricInterval
  } else {
    delete env[FIRST_CLASS_METRIC_INTERVAL_KEY]
  }

  for (const [signalName, exporterKey] of Object.entries(
    FIRST_CLASS_SIGNAL_KEYS
  )) {
    if (form.signals[signalName as keyof AgentOtelSignals]) {
      env[exporterKey] = "otlp"
    } else {
      delete env[exporterKey]
    }
  }

  return env
}

/**
 * Serialize the whole form (first-class fields merged with the advanced tail)
 * into raw `KEY=value` editor text, sorted by key. This is the text shown by
 * the Raw editing mode, so it must round-trip with {@link parseEnvText} +
 * {@link envMapToForm}.
 */
export function formToEnvText(form: AgentOtelForm): string {
  return envMapToText(formToEnvMap(form))
}

/**
 * Parse raw `KEY=value` editor text back into the structured form. Inverse of
 * {@link formToEnvText}; used when leaving Raw mode so edits made as text are
 * reflected in the first-class fields.
 */
export function envTextToForm(text: string): AgentOtelForm {
  return envMapToForm(parseEnvText(text))
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

function parseResourceAttributes(value: string): Record<string, string> {
  const attributes: Record<string, string> = {}
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
    if (attributes[key] !== undefined) {
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
  if (POSITIVE_INTEGER_KEYS.has(spec.key) && !/^[1-9]\d*$/.test(value)) {
    issues.push(`${spec.key} must be a positive integer.`)
  }
  if (spec.key === "OTEL_RESOURCE_ATTRIBUTES") {
    try {
      parseResourceAttributes(value)
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
 * This is the shared validator behind both {@link validateEnvText} (the raw
 * editor) and {@link validateForm} (the structured form). It cannot detect
 * duplicate keys or empty values because a map has already collapsed those;
 * the text path handles those line-oriented checks separately.
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
    const generic = env[FIRST_CLASS_ENDPOINT_KEY]
    for (const exporterKey of SIGNAL_EXPORTER_KEYS) {
      const value = env[exporterKey]
      if (value === undefined) continue
      if (value.trim().toLowerCase() === "otlp" && !generic) {
        issues.push(`${exporterKey}=otlp needs ${FIRST_CLASS_ENDPOINT_KEY}.`)
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
  options?: EnvValidationOptions
): string[] {
  return validateEnvMap(formToEnvMap(form), options)
}

/**
 * Validate the env editor text against the same rules the backend enforces.
 * Returns a list of issues with their 1-indexed line numbers. Empty list
 * means the input is acceptable.
 */
export function validateEnvText(
  text: string,
  { requireOtlpEndpoint = true }: EnvValidationOptions = {}
): EnvIssue[] {
  const issues: EnvIssue[] = []
  const seen: Record<string, { lineNumber: number; value: string }> = {}
  const lines = text.split("\n")

  lines.forEach((rawLine, idx) => {
    const lineNumber = idx + 1
    const line = rawLine.trim()
    if (!line || line.startsWith("#")) {
      return
    }

    const sep = line.indexOf("=")
    if (sep <= 0) {
      issues.push({ lineNumber, message: "Expected KEY=value." })
      return
    }

    const key = line.slice(0, sep).trim()
    const value = line.slice(sep + 1).trim()
    const spec = OTEL_ENV_SPEC_BY_KEY.get(key)

    if (RESERVED_ENV_VARS.has(key)) {
      issues.push({ lineNumber, message: `${key} is managed by Tracecat.` })
      return
    }
    if (!spec) {
      issues.push({ lineNumber, message: `${key} is not supported.` })
      return
    }
    if (value === "") {
      issues.push({ lineNumber, message: `${key} needs a value.` })
      return
    }
    if (seen[key] !== undefined) {
      issues.push({ lineNumber, message: `${key} is duplicated.` })
      return
    }
    for (const message of envValueIssues(spec, value)) {
      issues.push({ lineNumber, message })
    }
    seen[key] = { lineNumber, value }
  })

  if (requireOtlpEndpoint) {
    const generic = seen.OTEL_EXPORTER_OTLP_ENDPOINT
    for (const exporterKey of SIGNAL_EXPORTER_KEYS) {
      const entry = seen[exporterKey]
      if (!entry) continue
      if (entry.value.trim().toLowerCase() === "otlp" && !generic) {
        issues.push({
          lineNumber: entry.lineNumber,
          message: `${exporterKey}=otlp needs OTEL_EXPORTER_OTLP_ENDPOINT.`,
        })
      }
    }
  }

  return issues
}

/**
 * Validate the headers editor text. Returns a list of human-readable error
 * messages (no line tagging since headers are JSON, not line-oriented).
 */
export function validateHeadersJson(text: string): string[] {
  if (text.trim() === "") {
    return []
  }
  try {
    const parsed: unknown = JSON.parse(text)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return ["Headers must be a JSON object."]
    }
    for (const [key, value] of Object.entries(parsed)) {
      if (!key.trim() || typeof value !== "string" || value.trim() === "") {
        return ["Headers must map non-empty names to non-empty string values."]
      }
    }
    return []
  } catch (error) {
    return [
      error instanceof Error ? error.message : "Headers must be valid JSON.",
    ]
  }
}

function lineDiagnostic(
  view: EditorView,
  lineNumber: number,
  message: string
): Diagnostic {
  const line = view.state.doc.line(lineNumber)
  const from = line.from
  const to = Math.max(from + 1, line.to)
  return { from, to, severity: "error", message }
}

function envCodeMirrorLinter(view: EditorView): Diagnostic[] {
  const text = view.state.doc.toString()
  return validateEnvText(text).map(({ lineNumber, message }) =>
    lineDiagnostic(view, lineNumber, message)
  )
}

function headersCodeMirrorLinter(view: EditorView): Diagnostic[] {
  const content = view.state.doc.toString()
  const issues = validateHeadersJson(content)
  if (issues.length === 0) return []
  // JSON-level error: highlight the whole document. JSON.parse error messages
  // can include "position N" which we use to pinpoint when present.
  const message = issues[0]
  const positionMatch = message.match(/position (\d+)/)
  if (positionMatch) {
    const pos = Number.parseInt(positionMatch[1], 10)
    const from = Math.min(pos, content.length)
    const to = Math.min(from + 1, content.length)
    return [{ from, to, severity: "error", message }]
  }
  return [
    {
      from: 0,
      to: Math.max(1, content.length),
      severity: "error",
      message,
    },
  ]
}

/** CodeMirror extensions for the agent OTel env editor. */
export const envLintExtensions: Extension[] = [
  lintGutter(),
  linter(envCodeMirrorLinter),
]

/** CodeMirror extensions for the agent OTel headers editor. */
export const headerLintExtensions: Extension[] = [
  lintGutter(),
  linter(headersCodeMirrorLinter),
]

/**
 * Parse the env editor text into a `KEY -> value` map. Skips blank lines and
 * `#` comments. The backend re-validates, so this is naive on purpose.
 */
export function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim()
    if (!line || line.startsWith("#")) {
      continue
    }
    const idx = line.indexOf("=")
    if (idx <= 0) {
      continue
    }
    const key = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim()
    if (key && value) {
      out[key] = value
    }
  }
  return out
}

/**
 * Parse the headers editor text into a `name -> value` map. Returns an empty
 * object for blank input. Throws if the JSON is invalid or not a flat
 * string-valued object.
 */
export function parseHeadersJson(text: string): Record<string, string> {
  if (text.trim() === "") {
    return {}
  }
  const parsed: unknown = JSON.parse(text)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Headers must be a JSON object.")
  }
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(parsed)) {
    if (typeof value !== "string") {
      throw new Error(`Header ${key} must be a string.`)
    }
    out[key] = value
  }
  return out
}

/** Serialize a `KEY -> value` map back into editor text, sorted by key. */
export function envMapToText(env: Record<string, string>): string {
  return Object.keys(env)
    .sort()
    .map((key) => `${key}=${env[key]}`)
    .join("\n")
}
