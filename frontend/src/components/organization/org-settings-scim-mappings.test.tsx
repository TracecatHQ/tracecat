import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { ExternalGroupMappingRead } from "@/client"
import { OrgSettingsScimConnection } from "@/components/organization/org-settings-scim-connection"
import { OrgSettingsScimMappings } from "@/components/organization/org-settings-scim-mappings"
import { TooltipProvider } from "@/components/ui/tooltip"

let mappings: ExternalGroupMappingRead[] = []
const createMapping = jest.fn()
const deleteMapping = jest.fn()
const review = { mutateAsync: jest.fn(), isPending: false }
const activate = { mutateAsync: jest.fn(), isPending: false }

let allowedScopes: string[] | null = null
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    allowedScopes === null || allowedScopes.includes(scope),
}))
jest.mock("@/lib/api", () => ({
  getBaseUrl: () => "https://api.example.com/backend/",
}))

jest.mock("@/hooks/use-scim", () => ({
  useScimExternalGroups: () => ({
    externalGroups: [
      {
        id: "source",
        external_id: "idp-source",
        display_name: "IdP team",
        member_count: 2,
      },
    ],
  }),
  useScimMappings: () => ({ mappings, createMapping, deleteMapping }),
  useScimActivation: () => ({ review, activate }),
  useScimConnection: () => ({
    connection: {
      id: "connection",
      organization_id: "org",
      status: "pending",
      preview: "scim_test",
      revoked_at: null,
      last_used_at: null,
    },
    issueToken: jest.fn(),
    revokeToken: jest.fn(),
  }),
}))
jest.mock("@/lib/hooks", () => ({
  useRbacGroups: () => ({ groups: [{ id: "target", name: "Target team" }] }),
  useOrgMembers: () => ({
    orgMembers: [{ user_id: "manual", email: "manual@example.com" }],
  }),
}))
jest.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string
    onValueChange: (value: string) => void
    children: ReactNode
  }) => (
    <select
      value={value}
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
  users: [{ id: "user", email: "eligible@example.com", active: true }],
  groups: [],
  plans: [
    {
      external_group_id: "source",
      external_group_display_name: "IdP team",
      group_id: "target",
      group_name: "Target team",
      manual_members_purged: ["manual"],
    },
  ],
}

beforeEach(() => {
  jest.clearAllMocks()
  mappings = []
  allowedScopes = null
  review.mutateAsync.mockResolvedValue(preview)
  activate.mutateAsync.mockResolvedValue(undefined)
  createMapping.mockResolvedValue(undefined)
})

function selectMapping() {
  const selectors = screen.getAllByRole("combobox")
  fireEvent.change(selectors[0], { target: { value: "source" } })
  fireEvent.change(selectors[1], { target: { value: "target" } })
}

test("pending mappings stay local until reviewed activation", async () => {
  render(<OrgSettingsScimMappings connected status="pending" revoked={false} />)
  selectMapping()
  fireEvent.click(screen.getByRole("button", { name: "Add draft mapping" }))
  expect(createMapping).not.toHaveBeenCalled()
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Review activation" }))
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(screen.getByText(/eligible@example.com/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Confirm activation" }))
  await waitFor(() =>
    expect(activate.mutateAsync).toHaveBeenCalledWith([
      { external_group_id: "source", group_id: "target" },
    ])
  )
  expect(createMapping).not.toHaveBeenCalled()
})

test("active mapping discloses purge before committing", async () => {
  render(<OrgSettingsScimMappings connected status="active" revoked={false} />)
  selectMapping()
  fireEvent.click(screen.getByRole("button", { name: "Review mapping" }))
  expect(await screen.findByText("manual@example.com")).toBeInTheDocument()
  expect(createMapping).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Confirm mapping" }))
  await waitFor(() =>
    expect(createMapping).toHaveBeenCalledWith({
      externalGroupId: "source",
      groupId: "target",
    })
  )
})

test("cancelling review leaves memberships untouched", async () => {
  render(<OrgSettingsScimMappings connected status="pending" revoked={false} />)
  fireEvent.click(screen.getByRole("button", { name: "Review activation" }))
  await screen.findByText("manual@example.com")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(activate.mutateAsync).not.toHaveBeenCalled()
  expect(createMapping).not.toHaveBeenCalled()
})

test("pending connection is not labelled active", () => {
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  expect(screen.getByText("Pending activation")).toBeInTheDocument()
  expect(screen.queryByText("Active")).not.toBeInTheDocument()
})

test("last unmap explains retained access before removal", () => {
  mappings = [
    {
      id: "mapping",
      external_group_id: "source",
      external_group_external_id: "external",
      external_group_display_name: "IdP team",
      group_id: "target",
      group_name: "Target team",
    },
  ]
  render(<OrgSettingsScimMappings connected status="active" revoked={false} />)
  fireEvent.click(
    screen.getByRole("button", { name: "Remove mapping for IdP team" })
  )
  expect(screen.getByText(/retained as manual members/)).toBeInTheDocument()
  expect(deleteMapping).not.toHaveBeenCalled()
})

test("confirmation uses the reviewed mapping if selectors changed during loading", async () => {
  render(<OrgSettingsScimMappings connected status="active" revoked={false} />)
  selectMapping()
  fireEvent.click(screen.getByRole("button", { name: "Review mapping" }))
  for (const selector of screen.getAllByRole("combobox")) {
    fireEvent.change(selector, { target: { value: "" } })
  }
  await screen.findByText("manual@example.com")
  fireEvent.click(screen.getByRole("button", { name: "Confirm mapping" }))
  await waitFor(() =>
    expect(createMapping).toHaveBeenCalledWith({
      externalGroupId: "source",
      groupId: "target",
    })
  )
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
])("token rotation requires all issuance scopes: %j", ({ scopes }) => {
  allowedScopes = scopes
  render(
    <TooltipProvider>
      <OrgSettingsScimConnection />
    </TooltipProvider>
  )
  expect(screen.getByRole("button", { name: "Rotate token" })).toBeDisabled()
})

test("read-only users cannot create, activate, or remove mappings", () => {
  allowedScopes = ["org:rbac:read"]
  mappings = [
    {
      id: "mapping",
      external_group_id: "source",
      group_id: "target",
      external_group_external_id: "idp-source",
      external_group_display_name: "IdP team",
      group_name: "Target team",
    },
  ]
  const { rerender } = render(
    <OrgSettingsScimMappings connected status="active" revoked={false} />
  )
  selectMapping()
  expect(screen.getByRole("button", { name: "Review mapping" })).toBeDisabled()
  expect(
    screen.getByRole("button", { name: "Remove mapping for IdP team" })
  ).toBeDisabled()
  rerender(
    <OrgSettingsScimMappings connected status="pending" revoked={false} />
  )
  expect(
    screen.getByRole("button", { name: "Review activation" })
  ).toBeDisabled()
})
