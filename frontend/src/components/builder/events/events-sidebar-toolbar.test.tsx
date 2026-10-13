import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { EventsSidebarToolbar } from "@/components/builder/events/events-sidebar-toolbar"
import type { FormsAccess } from "@/lib/forms/access"

let mockFormsAccess: FormsAccess = "enabled"

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))

jest.mock("@/components/builder/events/events-workflow", () => ({
  getTriggerTypeIcon: () => null,
}))

async function openFilter() {
  render(<EventsSidebarToolbar />)
  await userEvent.click(screen.getByRole("button", { name: "Filter" }))
  return screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)
}

beforeEach(() => {
  localStorage.clear()
})

describe("EventsSidebarToolbar trigger filter", () => {
  it("has no Form entry with the forms feature flag off", async () => {
    mockFormsAccess = "hidden"

    const entries = await openFilter()

    expect(entries).not.toContain("Form")
    expect(entries).toEqual(
      expect.arrayContaining(["Manual", "Scheduled", "Webhook", "Case"])
    )
  })

  it.each<FormsAccess>(["loading", "locked", "enabled"])(
    "offers Form when Forms is %s",
    async (access) => {
      mockFormsAccess = access

      expect(await openFilter()).toContain("Form")
    }
  )
})
