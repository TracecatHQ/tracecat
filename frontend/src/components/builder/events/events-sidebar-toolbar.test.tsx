import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { EventsSidebarToolbar } from "@/components/builder/events/events-sidebar-toolbar"
import type { FormsAccess } from "@/hooks/use-forms-access"

let access: FormsAccess = "enabled"

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => access,
}))
jest.mock("@/components/builder/events/events-workflow", () => ({
  getTriggerTypeIcon: () => null,
}))

it.each<[FormsAccess, boolean]>([
  ["hidden", false],
  ["loading", true],
  ["locked", true],
  ["enabled", true],
])(
  "offers the Form trigger filter when Forms is %s: %p",
  async (value, offered) => {
    access = value
    render(<EventsSidebarToolbar />)

    await userEvent.click(screen.getByRole("button", { name: "Filter" }))

    const entries = screen
      .getAllByRole("menuitemcheckbox")
      .map((item) => item.textContent)
    expect(entries.includes("Form")).toBe(offered)
    expect(entries).toContain("Webhook")
  }
)
