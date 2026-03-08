import { fireEvent, render, screen } from "@testing-library/react"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupTextarea,
} from "@/components/ui/input-group"

describe("InputGroupAddon", () => {
  it("focuses textarea controls when clicked", () => {
    render(
      <InputGroup>
        <InputGroupAddon data-testid="addon">Attach</InputGroupAddon>
        <InputGroupTextarea data-testid="textarea" />
      </InputGroup>
    )

    const addon = screen.getByTestId("addon")
    const textarea = screen.getByTestId("textarea")

    expect(textarea).not.toHaveFocus()
    fireEvent.click(addon)
    expect(textarea).toHaveFocus()
  })
})
