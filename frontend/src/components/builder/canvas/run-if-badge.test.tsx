import { render, screen } from "@testing-library/react"
import {
  formatRunIfCondition,
  RunIfBadge,
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
