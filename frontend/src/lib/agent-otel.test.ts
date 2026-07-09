import {
  type AgentOtelForm,
  envMapToForm,
  envTextToForm,
  formToEnvMap,
  formToEnvText,
  parseEnvText,
  validateEnvMap,
  validateForm,
} from "@/lib/agent-otel"

describe("envMapToForm", () => {
  it("pulls first-class keys into dedicated fields", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://collector.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.endpoint).toBe("https://collector.example.com")
    expect(form.metricIntervalMs).toBe("60000")
    expect(form.signals).toEqual({ traces: true, metrics: true, logs: false })
    expect(form.advancedEnv).toBe("")
  })

  it("keeps non-first-class keys in the advanced editor", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_RESOURCE_ATTRIBUTES: "service.name=agent",
      OTEL_LOG_USER_PROMPTS: "true",
    })

    expect(form.endpoint).toBe("https://c.example.com")
    // Sorted KEY=value text of the remaining keys.
    expect(form.advancedEnv).toBe(
      "OTEL_LOG_USER_PROMPTS=true\nOTEL_RESOURCE_ATTRIBUTES=service.name=agent"
    )
  })

  it("leaves a non-otlp exporter value in advanced rather than lighting the toggle", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "console",
      OTEL_METRICS_EXPORTER: "otlp,console",
    })

    // Neither is a bare `otlp`, so both stay in advanced and toggles stay off.
    expect(form.signals).toEqual({ traces: false, metrics: false, logs: false })
    expect(parseEnvText(form.advancedEnv)).toEqual({
      OTEL_TRACES_EXPORTER: "console",
      OTEL_METRICS_EXPORTER: "otlp,console",
    })
  })
})

describe("formToEnvMap", () => {
  const base: AgentOtelForm = {
    endpoint: "",
    metricIntervalMs: "",
    signals: { traces: false, metrics: false, logs: false },
    advancedEnv: "",
  }

  it("omits empty first-class fields entirely", () => {
    expect(formToEnvMap(base)).toEqual({})
  })

  it("writes trimmed first-class fields and otlp exporters for on signals", () => {
    const env = formToEnvMap({
      ...base,
      endpoint: "  https://c.example.com  ",
      metricIntervalMs: " 30000 ",
      signals: { traces: true, metrics: false, logs: true },
    })

    expect(env).toEqual({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "30000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
    })
  })

  it("lets first-class fields win over advanced on key collision", () => {
    const env = formToEnvMap({
      ...base,
      endpoint: "https://first-class.example.com",
      advancedEnv: "OTEL_EXPORTER_OTLP_ENDPOINT=https://advanced.example.com",
    })

    expect(env.OTEL_EXPORTER_OTLP_ENDPOINT).toBe(
      "https://first-class.example.com"
    )
  })
})

describe("round-trip fidelity", () => {
  it.each<Record<string, string>>([
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
      OTEL_RESOURCE_ATTRIBUTES: "service.name=agent",
    },
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "console",
      OTEL_LOG_USER_PROMPTS: "true",
    },
    {},
  ])("env -> form -> env is identity for %o", (env) => {
    expect(formToEnvMap(envMapToForm(env))).toEqual(env)
  })
})

describe("Raw mode helpers", () => {
  it("formToEnvText serializes the whole form as sorted KEY=value text", () => {
    const text = formToEnvText({
      endpoint: "https://c.example.com",
      metricIntervalMs: "",
      signals: { traces: true, metrics: false, logs: false },
      advancedEnv: "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
    })

    expect(text).toBe(
      [
        "OTEL_EXPORTER_OTLP_ENDPOINT=https://c.example.com",
        "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
        "OTEL_TRACES_EXPORTER=otlp",
      ].join("\n")
    )
  })

  it("form -> text -> form is identity through the Raw round-trip", () => {
    const form: AgentOtelForm = {
      endpoint: "https://c.example.com",
      metricIntervalMs: "60000",
      signals: { traces: true, metrics: true, logs: false },
      advancedEnv: "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
    }
    expect(envTextToForm(formToEnvText(form))).toEqual(form)
  })
})

describe("validateForm / validateEnvMap", () => {
  it("accepts a well-formed config", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "otlp",
    })
    expect(validateForm(form)).toEqual([])
  })

  it("flags an otlp signal with no endpoint", () => {
    const form: AgentOtelForm = {
      endpoint: "",
      metricIntervalMs: "",
      signals: { traces: true, metrics: false, logs: false },
      advancedEnv: "",
    }
    const issues = validateForm(form)
    expect(issues.length).toBeGreaterThan(0)
    expect(issues[0]).toContain("OTEL_TRACES_EXPORTER=otlp needs")
  })

  it("rejects an unsupported key in the merged map", () => {
    expect(validateEnvMap({ NOT_AN_OTEL_VAR: "x" })).toEqual([
      "NOT_AN_OTEL_VAR is not supported.",
    ])
  })

  it("rejects a reserved key", () => {
    expect(validateEnvMap({ OTEL_EXPORTER_OTLP_HEADERS: "x" })).toEqual([
      "OTEL_EXPORTER_OTLP_HEADERS is managed by Tracecat.",
    ])
  })

  it("rejects a non-positive interval", () => {
    expect(validateEnvMap({ OTEL_METRIC_EXPORT_INTERVAL: "0" })).toEqual([
      "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer.",
    ])
  })

  it.each([
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
  ])("rejects relay-managed protocol key %s", (key) => {
    expect(validateEnvMap({ [key]: "http/protobuf" })).toEqual([
      `${key} is managed by Tracecat.`,
    ])
  })
})
