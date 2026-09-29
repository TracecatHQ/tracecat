import { render, screen } from "@testing-library/react"
import {
  formatRunIfCondition,
  RunIfBadge,
  splitRunIfCondition,
} from "@/components/builder/canvas/run-if-badge"

const RUN_IF = "${{ FN.length(ACTIONS.fetch_events.result) > 0 }}"

describe("formatRunIfCondition", () => {
  it("strips the template wrapper", () => {
    expect(formatRunIfCondition("${{ var.item.severity == 'high' }}")).toBe(
      "var.item.severity == 'high'"
    )
  })
})

describe("RunIfBadge", () => {
  it("shows only the icon when compact and not expanded", () => {
    render(<RunIfBadge runIf={RUN_IF} compact expanded={false} />)
    const badge = screen.getByLabelText(/^Run if /)
    expect(badge).toHaveAttribute("data-state", "collapsed")
    expect(badge.querySelector("pre")).toBeNull()
  })

  it("shows the full condition when compact and expanded", () => {
    render(<RunIfBadge runIf={RUN_IF} compact expanded />)
    const badge = screen.getByLabelText(/^Run if /)
    expect(badge).toHaveAttribute("data-state", "expanded")
    expect(badge.querySelector("pre")?.textContent).toBe(
      formatRunIfCondition(RUN_IF)
    )
  })

  it("always shows the full condition when compact mode is off", () => {
    render(<RunIfBadge runIf={RUN_IF} compact={false} expanded={false} />)
    const badge = screen.getByLabelText(/^Run if /)
    expect(badge).toHaveAttribute("data-state", "expanded")
    expect(badge.querySelector("pre")).not.toBeNull()
  })
})

describe("splitRunIfCondition", () => {
  it("splits on top-level operators and keeps the operator per line", () => {
    expect(
      splitRunIfCondition(
        "@normalize != None && TRIGGER.env == 'prod' || TRIGGER.force"
      )
    ).toEqual([
      { operator: null, text: "@normalize != None" },
      { operator: "&&", text: "TRIGGER.env == 'prod'" },
      { operator: "||", text: "TRIGGER.force" },
    ])
  })

  it("ignores operators inside strings and nested groups", () => {
    expect(
      splitRunIfCondition("TRIGGER.q == 'a && b' && (TRIGGER.x || TRIGGER.y)")
    ).toEqual([
      { operator: null, text: "TRIGGER.q == 'a && b'" },
      { operator: "&&", text: "(TRIGGER.x || TRIGGER.y)" },
    ])
  })

  it("unwraps a condition fully wrapped in parentheses", () => {
    expect(splitRunIfCondition("(TRIGGER.x && TRIGGER.y)")).toEqual([
      { operator: null, text: "TRIGGER.x" },
      { operator: "&&", text: "TRIGGER.y" },
    ])
    expect(splitRunIfCondition("(TRIGGER.x) && (TRIGGER.y)")).toHaveLength(2)
  })
})

describe("RunIfBadge stacked conditions", () => {
  const MULTI =
    "${{ ACTIONS.normalize.result != None && TRIGGER.severity == 'high' }}"

  it("stacks multi-clause conditions above an icon-only pill", () => {
    render(<RunIfBadge runIf={MULTI} compact expanded />)
    const badge = screen.getByLabelText(/^Run if /)
    expect(badge.querySelector("pre")).toBeNull()
    const stack = screen.getByTestId("run-if-stack")
    expect(stack.children).toHaveLength(2)
    expect(stack.textContent).toContain("TRIGGER.severity == 'high'")
  })

  it("hides the stack when collapsed", () => {
    render(<RunIfBadge runIf={MULTI} compact expanded={false} />)
    expect(screen.queryByTestId("run-if-stack")).toBeNull()
  })
})
