import { render, screen } from "@testing-library/react"
import type { FormFieldBlock } from "@/client"
import { DateWidget } from "@/components/forms/widgets/date"
import { DatetimeWidget } from "@/components/forms/widgets/datetime"
import type { FormWidgetProps } from "@/components/forms/widgets/types"

const field: FormFieldBlock = {
  type: "field",
  name: "needed_by",
  label: "Needed by",
  widget: "date",
  required: true,
  multiple: false,
}

function props(value: unknown): FormWidgetProps {
  return {
    id: "needed_by",
    field,
    value,
    onChange: jest.fn(),
    onBlur: jest.fn(),
    disabled: false,
    invalid: false,
    runtime: { formId: "form-1", workspaceId: "workspace-1", mode: "live" },
  }
}

// The field row renders the label; the widgets only point at it.
function Label() {
  return (
    <label id="needed_by-label" htmlFor="needed_by">
      Needed by
    </label>
  )
}

describe("date widgets", () => {
  it("names the date button by its label and the picked date", () => {
    render(
      <>
        <Label />
        <DateWidget {...props("2026-10-22")} />
      </>
    )

    expect(
      screen.getByRole("button", { name: "Needed by Oct 22, 2026" })
    ).toBeVisible()
  })

  it("names an empty date button by its label and placeholder", () => {
    render(
      <>
        <Label />
        <DateWidget {...props(null)} />
      </>
    )

    expect(
      screen.getByRole("button", { name: "Needed by Pick a date" })
    ).toBeVisible()
  })

  it("names the datetime button the same way", () => {
    render(
      <>
        <Label />
        <DatetimeWidget
          {...props(new Date(2026, 9, 22, 9, 30).toISOString())}
        />
      </>
    )

    expect(
      screen.getByRole("button", { name: "Needed by Oct 22, 2026" })
    ).toBeVisible()
  })
})
