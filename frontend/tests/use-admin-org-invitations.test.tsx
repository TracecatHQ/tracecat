/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  AdminListOrganizationInvitationsData,
  AdminListOrganizationInvitationsResponse,
  AdminOrgInvitationRead,
} from "@/client"
import { adminListOrganizationInvitations } from "@/client"
import { useAdminOrgInvitations } from "@/hooks/use-admin"

jest.mock("@/client", () => ({
  adminCreateOrganizationInvitation: jest.fn(),
  adminGetOrganizationInvitationToken: jest.fn(),
  adminListOrganizationInvitations: jest.fn(),
  adminRevokeOrganizationInvitation: jest.fn(),
}))

const mockListOrganizationInvitations =
  adminListOrganizationInvitations as jest.MockedFunction<
    typeof adminListOrganizationInvitations
  >

function createInvitation(id: string): AdminOrgInvitationRead {
  return {
    id,
    organization_id: "org-1",
    email: `${id}@example.com`,
    role_id: "role-1",
    role_name: "Organization member",
    role_slug: "organization-member",
    status: "pending",
    invited_by: null,
    expires_at: "2026-01-08T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    accepted_at: null,
    created_by_platform_admin: true,
  }
}

function createPage(
  items: AdminOrgInvitationRead[],
  overrides: Partial<AdminListOrganizationInvitationsResponse> = {}
): AdminListOrganizationInvitationsResponse {
  return {
    items,
    next_cursor: null,
    prev_cursor: null,
    has_more: false,
    has_previous: false,
    ...overrides,
  }
}

function resolvePage(
  page: AdminListOrganizationInvitationsResponse
): ReturnType<typeof adminListOrganizationInvitations> {
  return Promise.resolve(page) as ReturnType<
    typeof adminListOrganizationInvitations
  >
}

function HookProbe() {
  const {
    invitations,
    currentPage,
    hasNextPage,
    hasPreviousPage,
    goToNextPage,
    goToPreviousPage,
  } = useAdminOrgInvitations("org-1")

  return (
    <div>
      <span data-testid="first-email">{invitations[0]?.email ?? ""}</span>
      <span data-testid="current-page">{currentPage}</span>
      <span data-testid="has-next">{String(hasNextPage)}</span>
      <span data-testid="has-previous">{String(hasPreviousPage)}</span>
      <button onClick={goToNextPage} type="button">
        next
      </button>
      <button onClick={goToPreviousPage} type="button">
        previous
      </button>
    </div>
  )
}

function renderHookProbe() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <HookProbe />
    </QueryClientProvider>
  )
}

describe("useAdminOrgInvitations", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("passes cursor pagination params when moving between pages", async () => {
    const user = userEvent.setup()
    mockListOrganizationInvitations.mockImplementation(
      (params: AdminListOrganizationInvitationsData) => {
        if (params.cursor === "page-2-cursor") {
          return resolvePage(
            createPage([createInvitation("older")], {
              prev_cursor: "page-1-cursor",
              has_previous: true,
            })
          )
        }

        if (params.cursor === "page-1-cursor" && params.reverse) {
          return resolvePage(
            createPage([createInvitation("newer")], {
              next_cursor: "page-2-cursor",
              has_more: true,
            })
          )
        }

        return resolvePage(
          createPage([createInvitation("newer")], {
            next_cursor: "page-2-cursor",
            has_more: true,
          })
        )
      }
    )

    renderHookProbe()

    await waitFor(() => {
      expect(screen.getByTestId("first-email")).toHaveTextContent(
        "newer@example.com"
      )
    })
    expect(mockListOrganizationInvitations).toHaveBeenLastCalledWith({
      orgId: "org-1",
      limit: 20,
      cursor: null,
      reverse: false,
    })

    await user.click(screen.getByRole("button", { name: "next" }))

    await waitFor(() => {
      expect(screen.getByTestId("first-email")).toHaveTextContent(
        "older@example.com"
      )
    })
    expect(screen.getByTestId("current-page")).toHaveTextContent("1")
    expect(screen.getByTestId("has-previous")).toHaveTextContent("true")
    expect(mockListOrganizationInvitations).toHaveBeenLastCalledWith({
      orgId: "org-1",
      limit: 20,
      cursor: "page-2-cursor",
      reverse: false,
    })

    await user.click(screen.getByRole("button", { name: "previous" }))

    await waitFor(() => {
      expect(screen.getByTestId("first-email")).toHaveTextContent(
        "newer@example.com"
      )
    })
    expect(screen.getByTestId("current-page")).toHaveTextContent("0")
    expect(screen.getByTestId("has-next")).toHaveTextContent("true")
    expect(mockListOrganizationInvitations).toHaveBeenLastCalledWith({
      orgId: "org-1",
      limit: 20,
      cursor: "page-1-cursor",
      reverse: true,
    })
  })
})
