/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { CaseColumnBadge } from "@/components/cases/case-badge"

describe("CaseColumnBadge", () => {
  it("forwards DOM attributes and hover handlers", () => {
    const onPointerMove = jest.fn()

    render(
      <CaseColumnBadge
        label="Badge"
        data-testid="case-column-badge"
        aria-label="Custom field badge"
        onPointerMove={onPointerMove}
      />
    )

    const badge = screen.getByTestId("case-column-badge")
    expect(badge).toHaveAttribute("aria-label", "Custom field badge")

    fireEvent.pointerMove(badge)
    expect(onPointerMove).toHaveBeenCalled()
  })

  it("applies flex truncation classes to long labels", () => {
    render(<CaseColumnBadge label="A very long custom field label" />)

    const label = screen.getByText("A very long custom field label")
    const badge = label.parentElement

    expect(label).toHaveClass("min-w-0", "flex-1", "truncate")
    expect(badge).toHaveClass("min-w-0", "max-w-[120px]")
  })
})
