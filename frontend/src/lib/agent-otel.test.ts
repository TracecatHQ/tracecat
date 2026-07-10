import {
  type AgentOtelForm,
  envMapToForm,
  envTextToForm,
  formToEnvMap,
  formToEnvText,
  parseEnvText,
  validateEnvMap,
  validateEnvText,
  validateForm,
} from "@/lib/agent-otel"

describe("envMapToForm", () => {
  it("pulls first-class keys into dedicated fields", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://collector.example.com",
      OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.endpoint).toBe("https://collector.example.com")
    expect(form.protocol).toBe("http/protobuf")
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

  it("maps otlp to the toggle while preserving other exporters in advanced", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "console",
      OTEL_METRICS_EXPORTER: "otlp,console",
    })

    expect(form.signals).toEqual({ traces: false, metrics: true, logs: false })
    expect(parseEnvText(form.advancedEnv)).toEqual({
      OTEL_TRACES_EXPORTER: "console",
      OTEL_METRICS_EXPORTER: "console",
    })
  })
})

describe("formToEnvMap", () => {
  const base: AgentOtelForm = {
    endpoint: "",
    protocol: "",
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
      protocol: "grpc",
      metricIntervalMs: " 30000 ",
      signals: { traces: true, metrics: false, logs: true },
    })

    expect(env).toEqual({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_EXPORTER_OTLP_PROTOCOL: "grpc",
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

  it("removes only otlp when a mixed-exporter signal is unchecked", () => {
    const form = envMapToForm({
      OTEL_METRICS_EXPORTER: "otlp,console",
    })
    form.signals.metrics = false

    expect(formToEnvMap(form)).toEqual({
      OTEL_METRICS_EXPORTER: "console",
    })
  })
})

describe("round-trip fidelity", () => {
  it.each<Record<string, string>>([
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
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
      protocol: "grpc",
      metricIntervalMs: "",
      signals: { traces: true, metrics: false, logs: false },
      advancedEnv: "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
    })

    expect(text).toBe(
      [
        "OTEL_EXPORTER_OTLP_ENDPOINT=https://c.example.com",
        "OTEL_EXPORTER_OTLP_PROTOCOL=grpc",
        "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
        "OTEL_TRACES_EXPORTER=otlp",
      ].join("\n")
    )
  })

  it("form -> text -> form is identity through the Raw round-trip", () => {
    const form: AgentOtelForm = {
      endpoint: "https://c.example.com",
      protocol: "http/protobuf",
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
      protocol: "",
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

  it.each(["1e3", "1.0", "0x10"])(
    "rejects non-decimal interval value %s",
    (value) => {
      expect(validateEnvMap({ OTEL_METRIC_EXPORT_INTERVAL: value })).toEqual([
        "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer.",
      ])
    }
  )

  it("accepts free-form values for boolean-like keys accepted by the backend", () => {
    expect(validateEnvMap({ OTEL_LOG_USER_PROMPTS: "1" })).toEqual([])
  })

  it("accepts case-insensitive exporter and temporality values", () => {
    expect(
      validateEnvMap({
        OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
        OTEL_METRICS_EXPORTER: "OTLP,CONSOLE",
        OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "DELTA",
      })
    ).toEqual([])
  })

  it.each(["console,", ",console", "console,,"])(
    "ignores empty exporter tokens in %s",
    (value) => {
      expect(validateEnvMap({ OTEL_LOGS_EXPORTER: value })).toEqual([])
      expect(validateEnvText(`OTEL_LOGS_EXPORTER=${value}`)).toEqual([])
    }
  )

  it("keeps protocol validation case-sensitive and single-valued", () => {
    expect(
      validateEnvMap({ OTEL_EXPORTER_OTLP_PROTOCOL: "GRPC,http/json" })
    ).toEqual([
      "OTEL_EXPORTER_OTLP_PROTOCOL supports http/protobuf, http/json, grpc.",
    ])
  })

  it("accepts signal-specific protocol keys", () => {
    expect(
      validateEnvMap({ OTEL_EXPORTER_OTLP_LOGS_PROTOCOL: "http/protobuf" })
    ).toEqual([])
  })

  it("skips endpoint requirements when telemetry is disabled", () => {
    const options = { requireOtlpEndpoint: false }
    expect(validateEnvMap({ OTEL_LOGS_EXPORTER: "otlp" }, options)).toEqual([])
    expect(validateEnvText("OTEL_LOGS_EXPORTER=otlp", options)).toEqual([])
  })
})
