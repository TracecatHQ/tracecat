import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { EventsSidebarToolbar } from "@/components/builder/events/events-sidebar-toolbar"

jest.mock("@/components/builder/events/events-workflow", () => ({
  getTriggerTypeIcon: () => null,
}))

it("offers the Form trigger filter", async () => {
  render(<EventsSidebarToolbar />)

  await userEvent.click(screen.getByRole("button", { name: "Filter" }))

  const entries = screen
    .getAllByRole("menuitemcheckbox")
    .map((item) => item.textContent)
  expect(entries).toContain("Form")
  expect(entries).toContain("Webhook")
})
