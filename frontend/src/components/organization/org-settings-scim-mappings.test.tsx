import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ScimSettingsPage from "@/app/organization/settings/scim/page"
import type {
  ExternalGroupMappingRead,
  ExternalGroupRead,
  ScimConnectionRead,
} from "@/client"
import { OrgSettingsScim } from "@/components/organization/org-settings-scim"
import { OrgSettingsScimConnection } from "@/components/organization/org-settings-scim-connection"
import { OrganizationSidebar } from "@/components/sidebar/organization-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { TracecatApiError } from "@/lib/errors"

let mappings: ExternalGroupMappingRead[] = []
const applyMappingChanges = jest.fn()
const fetchNextMappings = jest.fn()
let mappingsHasNextPage = false
let mappingsIsFetchingNextPage = false
let mappingsError: Error | null = null
let mappingsIsLoading = false
const review = { mutateAsync: jest.fn(), isPending: false }
const activate = { mutateAsync: jest.fn(), isPending: false }
const refetchConnection = jest.fn()
const issueToken = jest.fn()
const disconnect = jest.fn()
const readConnection = jest.fn()
const initialConnection: ScimConnectionRead = {
  id: "connection",
  organization_id: "org",
  status: "pending",
  preview: "scim_test",
  revoked_at: null,
  last_used_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}
let connection: ScimConnectionRead | null | undefined = initialConnection
let connectionError: Pick<TracecatApiError, "status"> | null = null
let connectionIsFetching = false
const sourceGroup: ExternalGroupRead = {
  id: "source",
  external_id: "idp-source",
  display_name: "IdP team",
  member_count: 2,
}
let externalGroups: ExternalGroupRead[] = [sourceGroup]

let allowedScopes: string[] | null = null
let hasEntitlementData = true
let entitled = true
jest.mock("next/navigation", () => ({
  usePathname: () => "/organization/settings/scim",
}))
jest.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    allowedScopes === null || allowedScopes.includes(scope),
}))
jest.mock("@/lib/api", () => ({
  getBaseUrl: () => "https://api.example.com/backend/",
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => entitled,
    hasEntitlementData,
    isLoading: false,
  }),
}))

jest.mock("@/hooks/use-scim", () => ({
  useScimExternalGroups: () => ({ externalGroups }),
  useScimMappings: () => ({
    mappings,
    applyMappingChanges,
    mappingsHasNextPage,
    mappingsIsFetchingNextPage,
    mappingsError,
    mappingsIsLoading,
    fetchNextMappings,
  }),
  useScimActivation: () => ({ review, activate }),
  useScimDirectorySummary: () => ({
    directorySummary: {
      users: { total: 3, active: 2, inactive: 1 },
      groups: { total: 1, unmapped: 1 },
    },
  }),
  useScimConnection: () => {
    readConnection()
    return {
      connection,
      connectionError,
      connectionIsFetching,
      refetchConnection,
      issueToken,
      revokeToken: jest.fn(),
      disconnect,
    }
  },
}))
jest.mock("@/lib/hooks", () => ({
  useRbacGroups: () => ({ groups: [{ id: "target", name: "Target team" }] }),
  useOrgMembers: () => {
    throw new Error("SCIM review must not depend on org:member:read")
  },
}))

type ReviewPerson = { user_id: string; email: string; groups?: string[] }

function people(...items: ReviewPerson[]) {
  return { count: items.length, items }
}

const none = people()

const preview = {
  joining: people({ user_id: "user", email: "eligible@example.com" }),
  leaving: none,
  losing: people({
    user_id: "manual",
    email: "manual@example.com",
    groups: ["Target team"],
  }),
  to_idp: none,
  to_manual: none,
  groups: [
    {
      group_id: "target",
      group_name: "Target team",
      added_sources: ["IdP team"],
      removed_sources: [],
      gained: 1,
      lost: 1,
      takes_over: true,
    },
  ],
}

const mapped: ExternalGroupMappingRead = {
  id: "mapping",
  external_group_id: "source",
  external_group_external_id: "idp-source",
  external_group_display_name: "IdP team",
  group_id: "target",
  group_name: "Target team",
}

beforeEach(() => {
  jest.clearAllMocks()
  mappings = []
  mappingsHasNextPage = false
  mappingsIsFetchingNextPage = false
  mappingsError = null
  mappingsIsLoading = false
  allowedScopes = ["org:scim:manage"]
  hasEntitlementData = true
  entitled = true
  connection = initialConnection
  connectionError = null
  connectionIsFetching = false
  externalGroups = [sourceGroup]
  review.mutateAsync.mockResolvedValue(preview)
  activate.mutateAsync.mockResolvedValue(undefined)
  applyMappingChanges.mockResolvedValue(undefined)
})

function renderScim() {
  return render(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
}

const PICKER = "Tracecat groups for IdP team"

async function toggleTarget(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: PICKER }))
  await user.type(
    screen.getByPlaceholderText("Search Tracecat groups"),
    "target"
  )
  await user.click(screen.getByRole("option", { name: "Target team" }))
  if (screen.queryByRole("listbox")) await user.keyboard("{Escape}")
}

test("pending mappings stay local until reviewed activation", async () => {
  const user = userEvent.setup()
  renderScim()
  await toggleTarget(user)
  expect(screen.getByRole("button", { name: PICKER })).toHaveTextContent(
    "Target team"
  )
  expect(
    screen.getByText(/1 draft applies when you activate/)
  ).toBeInTheDocument()
  expect(applyMappingChanges).not.toHaveBeenCalled()
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  fireEvent.click(
    await screen.findByRole("button", { name: /loses? group access/ })
  )
  expect(screen.getByText("manual@example.com")).toBeInTheDocument()
  expect(review.mutateAsync).toHaveBeenCalledWith({
    mappings: [{ external_group_id: "source", group_id: "target" }],
    delete: [],
  })
  expect(screen.getByText("Removed from Target team")).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: /from your directory/ }))
  expect(screen.getByText("eligible@example.com")).toBeInTheDocument()
  // The preview already holds everyone, so nothing is refetched.
  expect(review.mutateAsync).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole("button", { name: "Activate for 1 user" }))
  await waitFor(() =>
    expect(activate.mutateAsync).toHaveBeenCalledWith([
      { external_group_id: "source", group_id: "target" },
    ])
  )
  expect(applyMappingChanges).not.toHaveBeenCalled()
})

test("a pending connection drafts without waiting on live mappings", () => {
  mappingsIsLoading = true
  const { rerender } = renderScim()
  expect(screen.getByRole("button", { name: PICKER })).toBeInTheDocument()
  mappingsIsLoading = false
  mappingsError = new Error("Unavailable")
  rerender(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
  expect(screen.getByRole("button", { name: PICKER })).toBeInTheDocument()
  expect(screen.queryByText("Unable to load group mappings")).toBeNull()
})

test("drafts stop at the review cap", () => {
  externalGroups = Array.from({ length: 101 }, (_, i) => ({
    ...sourceGroup,
    id: `source-${i}`,
    external_id: `idp-${i}`,
    display_name: `IdP team ${String(i).padStart(3, "0")}`,
  }))
  renderScim()
  for (const group of externalGroups) {
    fireEvent.click(
      screen.getByRole("button", {
        name: `Tracecat groups for ${group.display_name}`,
      })
    )
    fireEvent.click(screen.getByRole("option", { name: "Target team" }))
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: "Escape",
    })
  }
  expect(
    screen.getByText(/100 drafts apply when you activate/)
  ).toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Tracecat groups for IdP team 100" })
  ).not.toHaveTextContent("Target team")
}, 30_000)

test("discarding drafts clears them without writing", async () => {
  const user = userEvent.setup()
  renderScim()
  await toggleTarget(user)
  await user.click(screen.getByRole("button", { name: "Discard drafts" }))
  expect(screen.queryByText(/draft applies/)).toBeNull()
  expect(screen.getByRole("button", { name: PICKER })).toHaveTextContent(
    "Not mapped"
  )
})

test("drafts cannot be discarded while their review is loading", async () => {
  const user = userEvent.setup()
  const { rerender } = renderScim()
  await toggleTarget(user)
  review.isPending = true
  try {
    rerender(
      <TooltipProvider>
        <OrgSettingsScim />
      </TooltipProvider>
    )
    expect(
      screen.getByRole("button", { name: "Discard drafts" })
    ).toBeDisabled()
  } finally {
    review.isPending = false
  }
})

test("active changes stay drafts until one review applies them", async () => {
  const user = userEvent.setup()
  connection = { ...initialConnection, status: "active" }
  renderScim()
  await toggleTarget(user)
  expect(review.mutateAsync).not.toHaveBeenCalled()
  expect(screen.getByText(/1 draft applies after review/)).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Review changes" }))
  await user.click(
    await screen.findByRole("button", { name: /loses? group access/ })
  )
  expect(screen.getByText("manual@example.com")).toBeInTheDocument()
  expect(screen.getByText("Removed from Target team")).toBeInTheDocument()
  expect(screen.getByText("Added IdP team")).toBeInTheDocument()
  expect(screen.getByText("1 added · 1 removed")).toBeInTheDocument()
  expect(
    screen.getByText("Removes 1 previously added member.")
  ).toBeInTheDocument()
  // Joining the organization is an activation outcome only.
  expect(screen.queryByText("eligible@example.com")).toBeNull()
  expect(applyMappingChanges).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Apply 1 change" }))
  await waitFor(() =>
    expect(applyMappingChanges).toHaveBeenCalledWith({
      create: [{ external_group_id: "source", group_id: "target" }],
      delete: [],
    })
  )
})

test("missing affected-user labels prevent confirmation", async () => {
  connection = { ...initialConnection, status: "active" }
  review.mutateAsync.mockResolvedValue({
    ...preview,
    losing: people({ user_id: "manual", email: "", groups: ["Target team"] }),
  })
  const user = userEvent.setup()
  renderScim()
  await toggleTarget(user)
  await user.click(screen.getByRole("button", { name: "Review changes" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Affected user details could not be loaded"
  )
  expect(screen.getByRole("button", { name: "Apply 1 change" })).toBeDisabled()
  expect(applyMappingChanges).not.toHaveBeenCalled()
})

test("cancelling review leaves memberships untouched", async () => {
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  await screen.findByRole("button", { name: /loses? group access/ })
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  expect(applyMappingChanges).not.toHaveBeenCalled()
})

test("search narrows the group rows", () => {
  connection = { ...initialConnection, status: "active" }
  mappings = [mapped]
  externalGroups = [
    sourceGroup,
    {
      id: "other",
      external_id: "idp-other",
      display_name: "Ops",
      member_count: 1,
    },
  ]
  renderScim()
  expect(screen.getByText("Ops")).toBeInTheDocument()
  fireEvent.change(screen.getByRole("searchbox", { name: "Search groups" }), {
    target: { value: "idp t" },
  })
  expect(screen.getByText("IdP team")).toBeInTheDocument()
  expect(screen.queryByText("Ops")).toBeNull()
})

test("mapping pages load on request and removals apply after review", async () => {
  const user = userEvent.setup()
  connection = { ...initialConnection, status: "active" }
  mappingsHasNextPage = true
  mappings = [
    { ...mapped, id: "other-mapping", external_group_id: "other-source" },
  ]
  const { rerender } = renderScim()
  expect(screen.queryByRole("button", { name: PICKER })).toBeNull()
  expect(screen.queryByText("Not mapped")).toBeNull()
  expect(
    screen.getByText("Load all mappings to view selections.")
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Load more mappings" }))
  expect(fetchNextMappings).toHaveBeenCalledTimes(1)
  mappingsIsFetchingNextPage = true
  rerender(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
  expect(
    screen.getByRole("button", { name: "Loading mappings…" })
  ).toBeDisabled()
  mappingsIsFetchingNextPage = false
  mappingsError = new Error("Unavailable")
  rerender(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Unable to load more mappings"
  )
  expect(screen.queryByRole("button", { name: PICKER })).toBeNull()
  mappingsHasNextPage = false
  mappingsError = null
  mappings = [...mappings, mapped]
  rerender(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
  expect(screen.getByRole("button", { name: PICKER })).toHaveTextContent(
    "Target team"
  )
  review.mutateAsync.mockResolvedValue({
    ...preview,
    joining: none,
    losing: people({
      user_id: "gone",
      email: "gone@example.com",
      groups: ["Target team"],
    }),
    to_manual: people({
      user_id: "kept",
      email: "kept@example.com",
      groups: ["Target team"],
    }),
    groups: [
      {
        group_id: "target",
        group_name: "Target team",
        added_sources: [],
        removed_sources: ["IdP team"],
        gained: 0,
        lost: 1,
      },
    ],
  })
  await toggleTarget(user)
  expect(applyMappingChanges).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Review changes" }))
  expect(review.mutateAsync).toHaveBeenCalledWith({
    mappings: [],
    delete: ["mapping"],
  })
  await user.click(
    await screen.findByRole("button", { name: /loses? group access/ })
  )
  expect(screen.getByText("gone@example.com")).toBeInTheDocument()
  expect(screen.getByText("removed IdP team")).toBeInTheDocument()
  await user.click(
    screen.getByRole("button", { name: /1 IdP member becomes manual/ })
  )
  expect(screen.getByText("kept@example.com")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Apply 1 change" }))
  await waitFor(() =>
    expect(applyMappingChanges).toHaveBeenCalledWith({
      create: [],
      delete: ["mapping"],
    })
  )
})

test("generic RBAC permissions cannot administer mappings", () => {
  allowedScopes = [
    "org:rbac:read",
    "org:rbac:create",
    "org:rbac:update",
    "org:rbac:delete",
    "org:member:remove",
  ]
  connection = { ...initialConnection, status: "active" }
  mappings = [mapped]
  const { unmount } = renderScim()
  expect(screen.getByRole("button", { name: PICKER })).toBeDisabled()
  unmount()
  connection = initialConnection
  mappings = []
  renderScim()
  expect(screen.getByRole("button", { name: PICKER })).toBeDisabled()
  expect(
    screen.getByRole("button", { name: "Review and activate" })
  ).toBeDisabled()
})

test("pending connection is labelled pending, with directory counts", () => {
  renderScim()
  expect(screen.getByText("Pending")).toBeInTheDocument()
  expect(screen.queryByText("Active")).not.toBeInTheDocument()
  expect(
    screen.getByText(
      (_, element) =>
        element?.tagName === "DD" &&
        element.textContent === "Pending·3 users (1 inactive)·1 group"
    )
  ).toBeInTheDocument()
})

test.each([undefined, initialConnection])(
  "connection errors block setup and rotation, including stale data: %j",
  (cached) => {
    connection = cached
    connectionError = Object.assign(new Error("Unavailable"), { status: 503 })
    const { rerender } = render(<ScimSettingsPage />)
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load SCIM connection"
    )
    expect(screen.queryByRole("button", { name: "Generate token" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Rotate token" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(refetchConnection).toHaveBeenCalledTimes(1)
    expect(issueToken).not.toHaveBeenCalled()
    connectionIsFetching = true
    rerender(<ScimSettingsPage />)
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled()
  }
)

test("a confirmed absent connection still offers setup", () => {
  connection = null
  render(<ScimSettingsPage />)
  expect(screen.getByRole("button", { name: "Generate token" })).toBeEnabled()
})

test("unknown plan access shows an error instead of an upgrade denial", () => {
  hasEntitlementData = false
  entitled = false
  const { rerender } = render(<ScimSettingsPage />, {
    wrapper: TooltipProvider,
  })
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Unable to check plan access"
  )
  expect(screen.getByRole("button", { name: "Reload" })).toBeEnabled()
  expect(screen.queryByText("Not available on your plan")).toBeNull()
  expect(readConnection).not.toHaveBeenCalled()
  hasEntitlementData = true
  rerender(<ScimSettingsPage />)
  expect(screen.getByText("Not available on your plan")).toBeInTheDocument()
  entitled = true
  rerender(<ScimSettingsPage />)
  expect(screen.getByText("Pending")).toBeInTheDocument()
})

test("a SCIM-only custom role can navigate to SCIM settings", () => {
  const { rerender } = render(
    <SidebarProvider>
      <OrganizationSidebar />
    </SidebarProvider>
  )
  expect(screen.getByRole("link", { name: "SCIM" })).toHaveAttribute(
    "href",
    "/organization/settings/scim"
  )
  expect(screen.queryByRole("link", { name: "Application" })).toBeNull()
  allowedScopes = []
  rerender(
    <SidebarProvider>
      <OrganizationSidebar />
    </SidebarProvider>
  )
  expect(screen.queryByRole("link", { name: "SCIM" })).toBeNull()
  expect(screen.queryByText("Settings")).toBeNull()
})

test("a new token is shown once alongside the base URL", async () => {
  connection = null
  issueToken.mockResolvedValue({ connection: initialConnection, token: "raw" })
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: "Generate token" }))
  expect(await screen.findByText("raw")).toBeInTheDocument()
  expect(
    screen.getByText("https://api.example.com/backend/scim/v2")
  ).toBeInTheDocument()
})

test("connection uses the configured API host and prefix", () => {
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  expect(
    screen.getByText("https://api.example.com/backend/scim/v2")
  ).toBeInTheDocument()
})

test.each([
  { scopes: [] },
  { scopes: ["org:rbac:create"] },
  { scopes: ["org:member:remove"] },
  { scopes: ["org:rbac:create", "org:rbac:delete", "org:member:remove"] },
])("token rotation requires explicit SCIM authority: %j", ({ scopes }) => {
  allowedScopes = scopes
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  expect(
    screen.getByRole("button", { name: "Connection actions" })
  ).toBeDisabled()
})

test("rotation asks for confirmation before issuing a token", async () => {
  const user = userEvent.setup()
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  await user.click(screen.getByRole("button", { name: "Connection actions" }))
  await user.click(screen.getByRole("menuitem", { name: "Rotate token" }))
  expect(
    screen.getByText(/current token stops working immediately/)
  ).toBeInTheDocument()
  expect(issueToken).not.toHaveBeenCalled()
})

test("users without SCIM authority cannot load the directory settings", () => {
  allowedScopes = ["org:rbac:read", "org:settings:read"]
  render(<ScimSettingsPage />)
  expect(screen.getByText("You lack permission")).toBeInTheDocument()
  expect(readConnection).not.toHaveBeenCalled()
})

test("SCIM authority alone enables token management", async () => {
  const user = userEvent.setup()
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  await user.click(screen.getByRole("button", { name: "Connection actions" }))
  expect(screen.getByRole("menuitem", { name: "Rotate token" })).toBeEnabled()
  expect(screen.getByRole("menuitem", { name: "Disconnect" })).toBeEnabled()
  expect(screen.queryByRole("menuitem", { name: /Revoke/ })).toBeNull()
})

test("disconnect confirms before detaching", async () => {
  const user = userEvent.setup()
  connection = { ...initialConnection, status: "active" }
  disconnect.mockResolvedValue(undefined)
  renderScim()
  await toggleTarget(user)
  expect(screen.getByText(/1 draft applies after review/)).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Connection actions" }))
  await user.click(screen.getByRole("menuitem", { name: "Disconnect" }))
  expect(screen.getByText(/nobody loses access/)).toBeInTheDocument()
  expect(disconnect).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Disconnect" }))
  await waitFor(() => expect(disconnect).toHaveBeenCalledTimes(1))
  expect(screen.queryByText(/draft applies/)).not.toBeInTheDocument()
})

test("show all fetches the full review once", async () => {
  const movers = Array.from({ length: 12 }, (_, index) => ({
    user_id: `mover-${index}`,
    email: `mover-${String(index).padStart(2, "0")}@example.com`,
    groups: ["Target team"],
  }))
  review.mutateAsync
    .mockResolvedValueOnce({
      ...preview,
      to_idp: { count: 12, items: movers.slice(0, 10) },
    })
    .mockResolvedValueOnce({ ...preview, to_idp: people(...movers) })
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  fireEvent.click(
    await screen.findByRole("button", {
      name: /12 manual members become IdP-managed/,
    })
  )
  expect(screen.getByText("mover-09@example.com")).toBeInTheDocument()
  expect(await screen.findByText("mover-11@example.com")).toBeInTheDocument()
  expect(review.mutateAsync).toHaveBeenLastCalledWith({
    mappings: [],
    delete: [],
    full: true,
  })
  fireEvent.click(screen.getByRole("button", { name: /from your directory/ }))
  expect(review.mutateAsync).toHaveBeenCalledTimes(2)
})

test("a disconnected directory offers a new token instead", async () => {
  const user = userEvent.setup()
  connection = {
    ...initialConnection,
    status: "disabled",
    revoked_at: "2026-01-02T00:00:00Z",
  }
  renderScim()
  expect(screen.getByText("Disconnected")).toBeInTheDocument()
  expect(screen.getByText("SCIM disconnected")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Connection actions" }))
  expect(
    screen.getByRole("menuitem", { name: "Generate new token" })
  ).toBeInTheDocument()
  expect(screen.queryByRole("menuitem", { name: "Disconnect" })).toBeNull()
})

test("a picker with several groups lists every name A to Z", () => {
  connection = { ...initialConnection, status: "active" }
  mappings = [
    { ...mapped, id: "third", group_id: "third", group_name: "Third team" },
    mapped,
    { ...mapped, id: "second", group_id: "other", group_name: "Other team" },
  ]
  renderScim()
  const picker = screen.getByRole("button", { name: PICKER })
  expect(picker).toHaveTextContent("Other teamTarget teamThird team")
})

test("activation discloses inactive members leaving the organization", async () => {
  review.mutateAsync.mockResolvedValue({
    ...preview,
    joining: people({ user_id: "joiner", email: "joiner@example.com" }),
    leaving: people({ user_id: "leaver", email: "leaver@example.com" }),
    losing: none,
    groups: [],
  })
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  fireEvent.click(
    await screen.findByRole("button", { name: /leaves? the organization/ })
  )
  expect(screen.getByText("leaver@example.com")).toBeInTheDocument()
  expect(
    screen.getByText(
      "1 person joins the organization and 1 person loses access."
    )
  ).toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Activate for 1 user" })
  ).toBeInTheDocument()
})

test("activation is not offered while the connection fails to load", () => {
  connectionError = { status: 500 }
  renderScim()
  expect(
    screen.queryByRole("button", { name: "Review and activate" })
  ).toBeNull()
})

test("confirmation waits for the full review to load", async () => {
  const { rerender } = renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  const confirm = await screen.findByRole("button", {
    name: "Activate for 1 user",
  })
  expect(confirm).toBeEnabled()
  review.isPending = true
  try {
    rerender(
      <TooltipProvider>
        <OrgSettingsScim />
      </TooltipProvider>
    )
    expect(
      screen.getByRole("button", { name: "Activate for 1 user" })
    ).toBeDisabled()
  } finally {
    review.isPending = false
  }
})
