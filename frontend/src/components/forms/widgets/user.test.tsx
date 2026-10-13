import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type FormFieldBlock,
  type FormUserRead,
  formsSearchFormUsers,
} from "@/client"
import { UserWidget } from "@/components/forms/widgets/user"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsSearchFormUsers: jest.fn(),
}))

const searchUsers = jest.mocked(formsSearchFormUsers)

const MEMBERS: FormUserRead[] = [
  {
    user_id: "user-avery",
    email: "avery.admin@example.com",
    first_name: "Avery",
    last_name: "Admin",
  },
  {
    user_id: "user-eli",
    email: "eli.editor@example.com",
    first_name: "Eli",
    last_name: "Editor",
  },
]

const field: FormFieldBlock = {
  type: "field",
  name: "manager",
  label: "Manager",
  widget: "user",
  required: false,
  multiple: false,
}

function renderWidget() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <UserWidget
      id="manager"
      field={field}
      value={null}
      onChange={jest.fn()}
      onBlur={jest.fn()}
      disabled={false}
      invalid={false}
      runtime={{ formId: "form-1", workspaceId: "workspace-1", mode: "live" }}
    />,
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
  )
}

describe("UserWidget search", () => {
  beforeEach(() => {
    searchUsers.mockReset()
    searchUsers.mockImplementation((({ search }: { search?: string } = {}) =>
      Promise.resolve({
        items: MEMBERS.filter((member) =>
          member.email.includes((search ?? "").toLowerCase())
        ),
        has_more: false,
        next_cursor: null,
      })) as unknown as typeof formsSearchFormUsers)
  })

  it("never shows the unfiltered list under a typed query", async () => {
    const user = userEvent.setup()
    renderWidget()

    await user.click(screen.getByRole("combobox"))
    expect(await screen.findByText("eli.editor@example.com")).toBeVisible()

    await user.type(
      screen.getByPlaceholderText("Search by name or email"),
      "av"
    )

    // The debounced request has not gone out yet: the old list must be gone.
    expect(screen.queryByText("eli.editor@example.com")).not.toBeInTheDocument()
    expect(screen.getByText("Searching...")).toBeVisible()

    expect(await screen.findByText("avery.admin@example.com")).toBeVisible()
    expect(screen.queryByText("eli.editor@example.com")).not.toBeInTheDocument()
    await waitFor(() =>
      expect(searchUsers).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "av" })
      )
    )
  })
})
