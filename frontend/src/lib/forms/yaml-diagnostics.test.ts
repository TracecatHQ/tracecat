import { getFormConfigIssues } from "@/lib/forms/errors"
import {
  type FormConfigIssue,
  locateFormIssues,
  parseFormConfigYaml,
  serializeFormConfig,
} from "@/lib/forms/yaml-diagnostics"

const CONFIG = `title: Laptop request
layout:
  - notice: Reviewed within 2 business days.
    tone: info
  - field: model
    widget: cards
    options:
      - value: mba
        label: MacBook Air
      - value: mbp
  - field: details
    visible_if: {field: reason, equals: replacment}
  - heading: Delivery
`

function issue(
  loc: FormConfigIssue["loc"],
  severity: FormConfigIssue["severity"] = "error"
): FormConfigIssue {
  return { severity, message: `at ${loc.join(".")}`, loc }
}

function marked(text: string, loc: FormConfigIssue["loc"]) {
  const [problem] = locateFormIssues(text, [issue(loc)])
  return { line: problem.line, text: text.slice(problem.from, problem.to) }
}

describe("parseFormConfigYaml", () => {
  it("reads YAML as the JSON config", () => {
    expect(parseFormConfigYaml(CONFIG)).toEqual({
      problems: [],
      config: {
        title: "Laptop request",
        layout: [
          { notice: "Reviewed within 2 business days.", tone: "info" },
          {
            field: "model",
            widget: "cards",
            options: [{ value: "mba", label: "MacBook Air" }, { value: "mbp" }],
          },
          {
            field: "details",
            visible_if: { field: "reason", equals: "replacment" },
          },
          { heading: "Delivery" },
        ],
      },
    })
  })

  it.each(["", "   \n", "# only a comment\n"])(
    "reads %j as an empty config",
    (text) => {
      expect(parseFormConfigYaml(text)).toEqual({ config: {}, problems: [] })
    }
  )

  it("reports a syntax error on its line and returns no config", () => {
    const text = "title: Laptop request\nlayout:\n  - field: [model\n"

    const { config, problems } = parseFormConfigYaml(text)

    expect(config).toBeNull()
    expect(problems.length).toBeGreaterThan(0)
    expect(problems[0].severity).toBe("error")
    expect(problems[0].line).toBeGreaterThanOrEqual(3)
    // One line of message, without the parser's code frame.
    expect(problems[0].message).not.toContain("\n")
    for (const problem of problems) {
      expect(problem.from).toBeLessThanOrEqual(text.length)
      expect(problem.to).toBeGreaterThanOrEqual(problem.from)
    }
  })

  it.each(["- field: model\n", "just text\n", "42\n"])(
    "refuses %j, which is not a mapping",
    (text) => {
      const { config, problems } = parseFormConfigYaml(text)

      expect(config).toBeNull()
      expect(problems).toEqual([
        expect.objectContaining({ severity: "error", line: 1, from: 0 }),
      ])
    }
  )
})

describe("locateFormIssues", () => {
  it("marks a key and its value on the right line", () => {
    expect(marked(CONFIG, ["layout", 1, "widget"])).toEqual({
      line: 6,
      text: "widget: cards",
    })
    expect(marked(CONFIG, ["title"])).toEqual({
      line: 1,
      text: "title: Laptop request",
    })
  })

  it("marks a value inside a flow mapping", () => {
    expect(marked(CONFIG, ["layout", 2, "visible_if", "equals"])).toEqual({
      line: 12,
      text: "equals: replacment",
    })
  })

  it("marks the first line of a layout entry", () => {
    expect(marked(CONFIG, ["layout", 1])).toEqual({
      line: 5,
      text: "field: model",
    })
    expect(marked(CONFIG, ["layout", 1, "options", 1])).toEqual({
      line: 10,
      text: "value: mbp",
    })
  })

  it("falls back to the deepest part of the path the text has", () => {
    // The entry has no `label` key, and there is no fifth entry.
    expect(marked(CONFIG, ["layout", 3, "label"]).line).toBe(13)
    expect(marked(CONFIG, ["layout", 9]).line).toBe(2)
    expect(marked(CONFIG, ["success_message"]).line).toBe(1)
    expect(marked("", ["layout"])).toEqual({ line: 1, text: "" })
  })

  it("skips the union tag in a request validation path", () => {
    expect(marked(CONFIG, ["layout", 1, "field", "widget"])).toEqual({
      line: 6,
      text: "widget: cards",
    })
    expect(marked(CONFIG, ["layout", 0, "notice", "tone"])).toEqual({
      line: 4,
      text: "tone: info",
    })
    // The field name itself is still reachable.
    expect(marked(CONFIG, ["layout", 1, "field"])).toEqual({
      line: 5,
      text: "field: model",
    })
  })

  it("lists errors before warnings, each in text order", () => {
    const problems = locateFormIssues(CONFIG, [
      issue(["layout", 3], "warning"),
      issue(["layout", 2, "visible_if"]),
      issue(["layout", 0], "warning"),
      issue(["title"]),
    ])

    expect(problems.map((problem) => [problem.severity, problem.line])).toEqual(
      [
        ["error", 1],
        ["error", 12],
        ["warning", 3],
        ["warning", 13],
      ]
    )
  })
})

describe("serializeFormConfig", () => {
  it("writes an empty config as empty text", () => {
    expect(serializeFormConfig({})).toBe("")
  })

  it("round-trips a config and keeps long values on one line", () => {
    const image = `data:image/webp;base64,${"A".repeat(4000)}`
    const config = {
      title: "Laptop request",
      description: `${"A long description. ".repeat(12)}`.trim(),
      layout: [{ field: "model", options: [{ value: "mba", image }] }],
    }

    const text = serializeFormConfig(config)

    expect(parseFormConfigYaml(text).config).toEqual(config)
    expect(text.split("\n").filter((line) => line.includes("data:"))).toEqual([
      expect.stringContaining(image),
    ])
    expect(text).toContain(`description: ${config.description}\n`)
  })
})

describe("getFormConfigIssues", () => {
  function apiError(status: number, detail: unknown) {
    return Object.assign(new Error("Request failed"), {
      status,
      body: { detail },
    })
  }

  it("reads the resolver's issues from form_config_invalid", () => {
    const issues = [
      {
        severity: "error",
        code: "incompatible_widget",
        message: "cards does not accept bool",
        loc: ["layout", 1, "widget"],
      },
    ]

    expect(
      getFormConfigIssues(
        apiError(422, {
          code: "form_config_invalid",
          message: "Invalid form config",
          issues,
        })
      )
    ).toEqual(issues)
  })

  it("reads request validation errors as issues inside the config", () => {
    const error = apiError(422, [
      {
        type: "enum",
        loc: ["body", "config", "layout", 0, "notice", "tone"],
        msg: "Input should be 'info', 'success', 'warning' or 'danger'",
      },
      { type: "missing", loc: ["body", "workflow_id"], msg: "Field required" },
    ])

    const issues = getFormConfigIssues(error)

    expect(issues).toEqual([
      {
        severity: "error",
        message: "Input should be 'info', 'success', 'warning' or 'danger'",
        loc: ["layout", 0, "notice", "tone"],
      },
      { severity: "error", message: "Field required", loc: [] },
    ])
    expect(locateFormIssues(CONFIG, issues ?? [])[1].line).toBe(4)
  })

  it.each([
    apiError(409, {
      code: "form_workflow_unpublished",
      message: "Unpublished",
    }),
    apiError(500, "Internal server error"),
    new Error("Network error"),
    null,
  ])("returns null for a failure that is not about the config", (error) => {
    expect(getFormConfigIssues(error)).toBeNull()
  })
})
