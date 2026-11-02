import { getFormConfigIssues } from "@/lib/forms/errors"
import {
  locateFormIssues,
  parseFormConfigYaml,
} from "@/lib/forms/yaml-diagnostics"

const CONFIG = `title: Laptop request
layout:
  - notice: Reviewed within 2 business days.
    tone: info
  - field: model
    widget: cards
  - field: details
    visible_if: {field: reason, equals: replacment}
  - heading: Delivery
`

it("reports a YAML syntax error inside the text and returns no config", () => {
  const text = "title: Laptop request\nlayout:\n  - field: [model\n"

  const { config, problems } = parseFormConfigYaml(text)

  expect(config).toBeNull()
  expect(problems[0]).toMatchObject({ severity: "error" })
  expect(problems[0].message).not.toContain("\n")
  expect(problems[0].to).toBeLessThanOrEqual(text.length)
})

it("reports an alias with no anchor on the alias and returns no config", () => {
  // The parser accepts it; only reading the document as JSON fails.
  const text = "title: *missing\nlayout: []\n"

  const { config, problems } = parseFormConfigYaml(text)

  expect(config).toBeNull()
  expect(problems).toHaveLength(1)
  expect(problems[0]).toMatchObject({ severity: "error" })
  expect(text.slice(problems[0].from, problems[0].to)).toBe("*missing")
})

it.each([
  [["layout", 1, "widget"], "widget: cards"],
  // Pydantic's union tag (`field`, `notice`) is not in the text.
  [["layout", 1, "field", "widget"], "widget: cards"],
  [["layout", 0, "notice", "tone"], "tone: info"],
  [["layout", 2, "visible_if", "equals"], "equals: replacment"],
  // A key the author left out lands on its parent.
  [["layout", 3, "label"], "heading: Delivery"],
])("marks an issue at %j on %p", (loc, marked) => {
  const [problem] = locateFormIssues(CONFIG, [
    { severity: "error", message: "Problem", loc },
  ])

  expect(CONFIG.slice(problem.from, problem.to)).toBe(marked)
})

it("reads a request validation error as an issue inside the config", () => {
  const msg = "Input should be 'info', 'success', 'warning' or 'danger'"
  const loc = ["body", "config", "layout", 0, "notice", "tone"]
  const error = { status: 422, body: { detail: [{ loc, msg }] } }

  expect(getFormConfigIssues(error)).toEqual([
    { severity: "error", message: msg, loc: loc.slice(2) },
  ])
})
