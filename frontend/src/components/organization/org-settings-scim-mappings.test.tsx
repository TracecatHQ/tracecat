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
import { TooltipProvider } from "@/components/ui/tooltip"
import type { TracecatApiError } from "@/lib/errors"

let mappings: ExternalGroupMappingRead[] = []
const applyMappingChanges = jest.fn()
const fetchNextMappings = jest.fn()
let mappingsHasNextPage = false
let mappingsIsFetchingNextPage = false
let mappingsError: Error | null = null
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
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    allowedScopes === null || allowedScopes.includes(scope),
}))
jest.mock("@/lib/api", () => ({
  getBaseUrl: () => "https://api.example.com/backend/",
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true, isLoading: false }),
}))

jest.mock("@/hooks/use-scim", () => ({
  useScimExternalGroups: () => ({ externalGroups }),
  useScimMappings: () => ({
    mappings,
    applyMappingChanges,
    mappingsHasNextPage,
    mappingsIsFetchingNextPage,
    mappingsError,
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

const preview = {
  users: [
    {
      id: "user",
      email: "eligible@example.com",
      external_id: "idp-user",
      active: true,
      is_member: false,
    },
  ],
  plans: [],
  groups: [
    {
      group_id: "target",
      group_name: "Target team",
      added_sources: ["IdP team"],
      removed_sources: [],
      changes: [
        {
          user_id: "manual",
          email: "manual@example.com",
          kind: "lose",
          from_source: "manual",
        },
        { user_id: "user", email: "eligible@example.com", kind: "gain" },
      ],
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
  allowedScopes = ["org:scim:manage"]
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
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(review.mutateAsync).toHaveBeenCalledWith({
    mappings: [{ external_group_id: "source", group_id: "target" }],
    delete: [],
  })
  fireEvent.click(screen.getByRole("button", { name: /Organization members/ }))
  expect(screen.getByText("joins")).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Activate for 1 user" }))
  await waitFor(() =>
    expect(activate.mutateAsync).toHaveBeenCalledWith([
      { external_group_id: "source", group_id: "target" },
    ])
  )
  expect(applyMappingChanges).not.toHaveBeenCalled()
})

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

test("active changes stay drafts until one review applies them", async () => {
  const user = userEvent.setup()
  connection = { ...initialConnection, status: "active" }
  renderScim()
  await toggleTarget(user)
  expect(review.mutateAsync).not.toHaveBeenCalled()
  expect(screen.getByText(/1 draft applies after review/)).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Review changes" }))
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(screen.getByText("manual → removed")).toBeInTheDocument()
  expect(screen.getByText("eligible@example.com")).toBeInTheDocument()
  expect(screen.queryByText("new → IdP")).toBeNull()
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
    groups: [
      {
        ...preview.groups[0],
        changes: [{ user_id: "manual", email: "", kind: "lose" }],
      },
    ],
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
  await screen.findByText("manual@example.com")
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
  mappings = [mapped]
  const { rerender } = renderScim()
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
  review.mutateAsync.mockResolvedValue({
    users: [],
    plans: [],
    groups: [
      {
        group_id: "target",
        group_name: "Target team",
        added_sources: [],
        removed_sources: ["IdP team"],
        changes: [
          {
            user_id: "kept",
            email: "kept@example.com",
            kind: "to_manual",
            from_source: "idp",
          },
          {
            user_id: "gone",
            email: "gone@example.com",
            kind: "lose",
            from_source: "idp",
          },
        ],
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
  expect(await screen.findByText("kept@example.com")).toBeInTheDocument()
  expect(screen.getByText("IdP → manual")).toBeInTheDocument()
  expect(screen.getByText("gone@example.com")).toBeInTheDocument()
  expect(screen.getByText("IdP → removed")).toBeInTheDocument()
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
  expect(screen.getByText("Pending activation")).toBeInTheDocument()
  expect(screen.queryByText("Active")).not.toBeInTheDocument()
  expect(
    screen.getByText(
      (_, element) =>
        element?.tagName === "DD" &&
        element.textContent === "3 users (1 inactive) · 1 group"
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

test("review caps source changes like gains", async () => {
  review.mutateAsync.mockResolvedValue({
    users: [],
    plans: [],
    groups: [
      {
        group_id: "target",
        group_name: "Target team",
        added_sources: ["IdP team"],
        removed_sources: [],
        changes: Array.from({ length: 12 }, (_, index) => ({
          user_id: `mover-${index}`,
          email: `mover-${String(index).padStart(2, "0")}@example.com`,
          kind: "to_idp" as const,
        })),
      },
    ],
  })
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  fireEvent.click(await screen.findByRole("button", { name: /Target team/ }))
  expect(screen.getByText("mover-09@example.com")).toBeInTheDocument()
  expect(screen.queryByText("mover-10@example.com")).not.toBeInTheDocument()
  expect(screen.getByText("2 more users")).toBeInTheDocument()
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
    users: [
      {
        id: "joiner",
        email: "joiner@example.com",
        external_id: "idp-joiner",
        active: true,
        is_member: false,
      },
      {
        id: "existing",
        email: "existing@example.com",
        external_id: "idp-existing",
        active: true,
        is_member: true,
      },
      {
        id: "leaver",
        email: "leaver@example.com",
        external_id: "idp-leaver",
        active: false,
        is_member: true,
      },
      {
        id: "stranger",
        email: "stranger@example.com",
        external_id: "idp-stranger",
        active: false,
        is_member: false,
      },
    ],
    plans: [],
    groups: [],
  })
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  expect(await screen.findByText("leaver@example.com")).toBeInTheDocument()
  expect(
    screen.getByText("inactive → leaves the organization")
  ).toBeInTheDocument()
  expect(screen.getByText("inactive, skipped")).toBeInTheDocument()
  expect(screen.queryByText("existing@example.com")).not.toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Activate for 1 user" })
  ).toBeInTheDocument()
})
