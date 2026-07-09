import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import type { Extension } from "@codemirror/state"
import type { EditorView } from "@codemirror/view"

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
])

interface OTelEnvSpec {
  key: string
  group: string
  values?: readonly string[]
  hint?: string
}

/**
 * Allowlisted Claude Code OTel env vars. Mirrors `ALLOWED` in
 * tracecat/agent/otel_config.py and the Claude Code monitoring docs:
 * https://code.claude.com/docs/en/monitoring-usage
 */
const OTEL_ENV_SPECS: readonly OTelEnvSpec[] = [
  {
    key: "OTEL_METRICS_EXPORTER",
    group: "Exporters",
    values: ["otlp", "prometheus", "console", "none"],
  },
  {
    key: "OTEL_LOGS_EXPORTER",
    group: "Exporters",
    values: ["otlp", "console", "none"],
  },
  {
    key: "OTEL_TRACES_EXPORTER",
    group: "Exporters",
    values: ["otlp", "console", "none"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_PROTOCOL",
    group: "OTLP",
    values: ["http/protobuf", "http/json", "grpc"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    group: "OTLP",
    values: ["http/protobuf", "http/json", "grpc"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    group: "OTLP",
    values: ["http/protobuf", "http/json", "grpc"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    group: "OTLP",
    values: ["http/protobuf", "http/json", "grpc"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_ENDPOINT",
    group: "OTLP",
    hint: "https://collector.example.com",
  },
  {
    key: "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    group: "OTLP",
    hint: "Optional signal override",
  },
  {
    key: "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    group: "OTLP",
    hint: "Optional signal override",
  },
  {
    key: "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    group: "OTLP",
    hint: "Optional signal override",
  },
  {
    key: "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS",
    group: "Intervals",
    hint: "Positive integer",
  },
  {
    key: "OTEL_METRIC_EXPORT_INTERVAL",
    group: "Intervals",
    hint: "Positive integer",
  },
  {
    key: "OTEL_LOGS_EXPORT_INTERVAL",
    group: "Intervals",
    hint: "Positive integer",
  },
  {
    key: "OTEL_TRACES_EXPORT_INTERVAL",
    group: "Intervals",
    hint: "Positive integer",
  },
  {
    key: "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",
    group: "Traces (beta)",
    values: ["true", "false"],
  },
  {
    key: "ENABLE_ENHANCED_TELEMETRY_BETA",
    group: "Traces (beta)",
    values: ["true", "false"],
  },
  {
    key: "ENABLE_BETA_TRACING_DETAILED",
    group: "Traces (beta)",
    values: ["true", "false"],
  },
  {
    key: "BETA_TRACING_ENDPOINT",
    group: "Traces (beta)",
    hint: "https://traces.example.com",
  },
  {
    key: "OTEL_LOG_USER_PROMPTS",
    group: "Content",
    values: ["true", "false"],
  },
  {
    key: "OTEL_LOG_TOOL_DETAILS",
    group: "Content",
    values: ["true", "false"],
  },
  {
    key: "OTEL_LOG_TOOL_CONTENT",
    group: "Content",
    values: ["true", "false"],
  },
  {
    key: "OTEL_LOG_RAW_API_BODIES",
    group: "Content",
    values: ["true", "false"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_SESSION_ID",
    group: "Metrics",
    values: ["true", "false"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_VERSION",
    group: "Metrics",
    values: ["true", "false"],
  },
  {
    key: "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
    group: "Metrics",
    values: ["true", "false"],
  },
  {
    key: "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    group: "Metrics",
    values: ["cumulative", "delta"],
  },
  {
    key: "OTEL_RESOURCE_ATTRIBUTES",
    group: "Resource",
    hint: "key=value,key=value",
  },
] as const

const OTEL_ENV_SPEC_BY_KEY: ReadonlyMap<string, OTelEnvSpec> = new Map(
  OTEL_ENV_SPECS.map((spec) => [spec.key, spec])
)

const POSITIVE_INTEGER_KEYS = new Set([
  "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS",
  "OTEL_METRIC_EXPORT_INTERVAL",
  "OTEL_LOGS_EXPORT_INTERVAL",
  "OTEL_TRACES_EXPORT_INTERVAL",
])

/**
 * Enum keys whose value is a comma-separated list of allowed tokens. The
 * backend only CSV-splits the signal exporter keys; every other enum key
 * (e.g. the OTLP protocol keys) must be a single literal value.
 */
const CSV_ENUM_KEYS = new Set([
  "OTEL_METRICS_EXPORTER",
  "OTEL_LOGS_EXPORTER",
  "OTEL_TRACES_EXPORTER",
])

const SIGNAL_ENDPOINT_KEYS = {
  OTEL_METRICS_EXPORTER: "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
  OTEL_LOGS_EXPORTER: "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
  OTEL_TRACES_EXPORTER: "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
} as const

/** A validation issue tied to a 1-indexed line in the env editor. */
export interface EnvIssue {
  lineNumber: number
  message: string
}

/**
 * First-class OTel env keys surfaced as dedicated form fields. Every other
 * allowlisted key stays reachable through the Advanced env editor.
 */
const FIRST_CLASS_ENDPOINT_KEY = "OTEL_EXPORTER_OTLP_ENDPOINT"
const FIRST_CLASS_PROTOCOL_KEY = "OTEL_EXPORTER_OTLP_PROTOCOL"
const FIRST_CLASS_METRIC_INTERVAL_KEY = "OTEL_METRIC_EXPORT_INTERVAL"

/**
 * Signal toggle key -> exporter env key. A signal that is ON writes
 * `<EXPORTER_KEY>=otlp`; OFF removes the key from the env map.
 */
const FIRST_CLASS_SIGNAL_KEYS = {
  traces: "OTEL_TRACES_EXPORTER",
  metrics: "OTEL_METRICS_EXPORTER",
  logs: "OTEL_LOGS_EXPORTER",
} as const

/** The set of env keys owned by first-class form fields. */
const FIRST_CLASS_KEYS: ReadonlySet<string> = new Set<string>([
  FIRST_CLASS_ENDPOINT_KEY,
  FIRST_CLASS_PROTOCOL_KEY,
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
 * raw `KEY=value` text. This is purely a presentation layer over the wire
 * format `env` map and never changes the API contract.
 */
export interface AgentOtelForm {
  /** OTEL_EXPORTER_OTLP_ENDPOINT value (empty string when unset). */
  endpoint: string
  /** OTEL_EXPORTER_OTLP_PROTOCOL value (empty string when unset). */
  protocol: string
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
 * exporter key resolves to exactly `otlp`; any exporter key with a non-`otlp`
 * value (e.g. `console`) is left in `advancedEnv` so nothing is silently
 * dropped and the round-trip stays faithful.
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
      // Only a bare `otlp` exporter maps cleanly to the toggle; anything else
      // (console/none/prometheus, or otlp mixed with others) stays in Advanced.
      if (value.trim() === "otlp") {
        signals[signalName] = true
      } else {
        advanced[key] = value
      }
      continue
    }
    if (!FIRST_CLASS_KEYS.has(key)) {
      advanced[key] = value
    }
  }

  return {
    endpoint: env[FIRST_CLASS_ENDPOINT_KEY] ?? "",
    protocol: env[FIRST_CLASS_PROTOCOL_KEY] ?? "",
    metricIntervalMs: env[FIRST_CLASS_METRIC_INTERVAL_KEY] ?? "",
    signals,
    advancedEnv: envMapToText(advanced),
  }
}

/**
 * Inverse of {@link envMapToForm}. Starts from the parsed `advancedEnv` tail
 * and overlays the first-class fields on top, so first-class fields win on key
 * collision. Non-empty first-class text fields are written trimmed; empty ones
 * are omitted entirely (no `KEY=`). Each ON signal writes
 * `<EXPORTER_KEY>=otlp`.
 */
export function formToEnvMap(form: AgentOtelForm): Record<string, string> {
  const env: Record<string, string> = parseEnvText(form.advancedEnv)

  const endpoint = form.endpoint.trim()
  if (endpoint) {
    env[FIRST_CLASS_ENDPOINT_KEY] = endpoint
  } else {
    delete env[FIRST_CLASS_ENDPOINT_KEY]
  }

  const protocol = form.protocol.trim()
  if (protocol) {
    env[FIRST_CLASS_PROTOCOL_KEY] = protocol
  } else {
    delete env[FIRST_CLASS_PROTOCOL_KEY]
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

function envValueIssues(spec: OTelEnvSpec, value: string): string[] {
  const issues: string[] = []
  if (spec.values) {
    const allowed = new Set<string>(spec.values)
    const parts = CSV_ENUM_KEYS.has(spec.key)
      ? value.split(",").map((part) => part.trim())
      : [value]
    if (parts.some((part) => !allowed.has(part))) {
      issues.push(`${spec.key} supports ${spec.values.join(", ")}.`)
    }
  }
  const numericValue = Number(value)
  if (
    POSITIVE_INTEGER_KEYS.has(spec.key) &&
    (!Number.isInteger(numericValue) || numericValue <= 0)
  ) {
    issues.push(`${spec.key} must be a positive integer.`)
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
export function validateEnvMap(env: Record<string, string>): string[] {
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

  const generic = env[FIRST_CLASS_ENDPOINT_KEY]
  for (const [exporterKey, endpointKey] of Object.entries(
    SIGNAL_ENDPOINT_KEYS
  )) {
    const value = env[exporterKey]
    if (value === undefined) continue
    const exporters = value.split(",").map((part) => part.trim())
    if (exporters.includes("otlp") && !generic && !env[endpointKey]) {
      issues.push(
        `${exporterKey}=otlp needs ${endpointKey} or ${FIRST_CLASS_ENDPOINT_KEY}.`
      )
    }
  }

  return issues
}

/**
 * Validate the structured form by materializing its merged env map via
 * {@link formToEnvMap} and running the shared {@link validateEnvMap} rules.
 * Returns human-readable messages; empty list means acceptable.
 */
export function validateForm(form: AgentOtelForm): string[] {
  return validateEnvMap(formToEnvMap(form))
}

/**
 * Validate the env editor text against the same rules the backend enforces.
 * Returns a list of issues with their 1-indexed line numbers. Empty list
 * means the input is acceptable.
 */
export function validateEnvText(text: string): EnvIssue[] {
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

  const generic = seen.OTEL_EXPORTER_OTLP_ENDPOINT
  for (const [exporterKey, endpointKey] of Object.entries(
    SIGNAL_ENDPOINT_KEYS
  )) {
    const entry = seen[exporterKey]
    if (!entry) continue
    const exporters = entry.value.split(",").map((part) => part.trim())
    if (exporters.includes("otlp") && !generic && !seen[endpointKey]) {
      issues.push({
        lineNumber: entry.lineNumber,
        message: `${exporterKey}=otlp needs ${endpointKey} or OTEL_EXPORTER_OTLP_ENDPOINT.`,
      })
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
