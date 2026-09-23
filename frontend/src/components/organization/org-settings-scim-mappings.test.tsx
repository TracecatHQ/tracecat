import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
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
const createMapping = jest.fn()
const deleteMapping = jest.fn()
const fetchNextMappings = jest.fn()
let mappingsHasNextPage = false
let mappingsIsFetchingNextPage = false
let mappingsError: Error | null = null
const review = { mutateAsync: jest.fn(), isPending: false }
const activate = { mutateAsync: jest.fn(), isPending: false }
const refetchConnection = jest.fn()
const issueToken = jest.fn()
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
    createMapping,
    deleteMapping,
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
    }
  },
}))
jest.mock("@/lib/hooks", () => ({
  useRbacGroups: () => ({ groups: [{ id: "target", name: "Target team" }] }),
  useOrgMembers: () => {
    throw new Error("SCIM review must not depend on org:member:read")
  },
}))
jest.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    disabled,
    onValueChange,
    children,
  }: {
    value: string
    disabled?: boolean
    onValueChange: (value: string) => void
    children: ReactNode
  }) => (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="" />
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}))

const preview = {
  users: [
    {
      id: "user",
      email: "eligible@example.com",
      external_id: "idp-user",
      active: true,
    },
  ],
  plans: [
    {
      external_group_id: "source",
      external_group_display_name: "IdP team",
      group_id: "target",
      group_name: "Target team",
      manual_members_purged: ["manual"],
      manual_member_emails: { manual: "manual@example.com" },
      users_gaining_access: ["user"],
      users_losing_access: ["manual"],
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
  createMapping.mockResolvedValue(undefined)
})

function renderScim() {
  return render(
    <TooltipProvider>
      <OrgSettingsScim />
    </TooltipProvider>
  )
}

function addTarget() {
  fireEvent.change(screen.getAllByRole("combobox")[0], {
    target: { value: "target" },
  })
}

test("pending mappings stay local until reviewed activation", async () => {
  renderScim()
  addTarget()
  expect(screen.getByText("Draft")).toBeInTheDocument()
  expect(screen.getByText(/applied only when you activate/)).toBeInTheDocument()
  expect(createMapping).not.toHaveBeenCalled()
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(review.mutateAsync).toHaveBeenCalledWith([
    { external_group_id: "source", group_id: "target" },
  ])
  fireEvent.click(
    screen.getByRole("button", { name: /Users joining the organization/ })
  )
  expect(screen.getByText("eligible@example.com")).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Activate for 1 user" }))
  await waitFor(() =>
    expect(activate.mutateAsync).toHaveBeenCalledWith([
      { external_group_id: "source", group_id: "target" },
    ])
  )
  expect(createMapping).not.toHaveBeenCalled()
})

test("discarding drafts clears them without writing", () => {
  renderScim()
  addTarget()
  fireEvent.click(screen.getByRole("button", { name: "Discard drafts" }))
  expect(screen.queryByText("Draft")).toBeNull()
  expect(screen.getByText("No access")).toBeInTheDocument()
})

test("active mapping discloses purge before committing", async () => {
  connection = { ...initialConnection, status: "active" }
  renderScim()
  addTarget()
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(screen.getByText(/Loses Target team access/)).toBeInTheDocument()
  expect(createMapping).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Apply mapping" }))
  await waitFor(() =>
    expect(createMapping).toHaveBeenCalledWith({
      externalGroupId: "source",
      groupId: "target",
    })
  )
})

test("missing affected-user labels prevent confirmation", async () => {
  connection = { ...initialConnection, status: "active" }
  review.mutateAsync.mockResolvedValue({
    ...preview,
    plans: [{ ...preview.plans[0], manual_member_emails: {} }],
  })
  renderScim()
  addTarget()
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Affected user details could not be loaded"
  )
  expect(screen.getByRole("button", { name: "Apply mapping" })).toBeDisabled()
  expect(createMapping).not.toHaveBeenCalled()
})

test("cancelling review leaves memberships untouched", async () => {
  renderScim()
  fireEvent.click(screen.getByRole("button", { name: "Review and activate" }))
  await screen.findByText("manual@example.com")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  expect(createMapping).not.toHaveBeenCalled()
})

test("search and the unmapped filter narrow the group rows", () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Unmapped 1" }))
  expect(screen.queryByText("IdP team")).toBeNull()
  expect(screen.getByText("Ops")).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "All 2" }))
  fireEvent.change(
    screen.getByRole("searchbox", { name: "Search identity provider groups" }),
    { target: { value: "idp t" } }
  )
  expect(screen.getByText("IdP team")).toBeInTheDocument()
  expect(screen.queryByText("Ops")).toBeNull()
})

test("mapping pages load on request and removal asks before deleting", () => {
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
  fireEvent.click(
    screen.getByRole("button", { name: "Remove Target team from IdP team" })
  )
  expect(screen.getByText(/retained as manual members/)).toBeInTheDocument()
  expect(deleteMapping).not.toHaveBeenCalled()
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
  expect(
    screen.getByRole("button", { name: "Remove Target team from IdP team" })
  ).toBeDisabled()
  unmount()
  connection = initialConnection
  mappings = []
  renderScim()
  expect(screen.getAllByRole("combobox")[0]).toBeDisabled()
  expect(
    screen.getByRole("button", { name: "Review and activate" })
  ).toBeDisabled()
})

test("pending connection is labelled pending, with directory counts", () => {
  renderScim()
  expect(screen.getByText("Pending activation")).toBeInTheDocument()
  expect(screen.queryByText("Active")).not.toBeInTheDocument()
  expect(screen.getByText("2 active · 1 inactive")).toBeInTheDocument()
  expect(screen.getByText("1 not mapped")).toBeInTheDocument()
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
  expect(screen.getByRole("button", { name: "Rotate token" })).toBeDisabled()
})

test("rotation asks for confirmation before issuing a token", () => {
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: "Rotate token" }))
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

test("SCIM authority alone enables token management", () => {
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  expect(screen.getByRole("button", { name: "Rotate token" })).toBeEnabled()
  expect(
    screen.getByRole("button", { name: "More connection actions" })
  ).toBeEnabled()
})
