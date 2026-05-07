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

function envValueIssues(spec: OTelEnvSpec, value: string): string[] {
  const issues: string[] = []
  if (spec.values) {
    const allowed = new Set<string>(spec.values)
    const parts = value.split(",").map((part) => part.trim())
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
