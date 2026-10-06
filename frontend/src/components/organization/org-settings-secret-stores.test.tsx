import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { SecretStoreRead, SecretStoreUpdate } from "@/client"
import { OrgSettingsSecretStores } from "@/components/organization/org-settings-secret-stores"
import { TooltipProvider } from "@/components/ui/tooltip"

const mockCreateStore = jest.fn()
const mockUpdateStore = jest.fn()
let mockStores: SecretStoreRead[] = []
let mockCanUpdate: boolean | undefined = true
let mockWorkspaces: { id: string; name: string }[] = []

jest.mock("@/components/auth/scope-guard", () => ({
  ScopeGuard: ({ children }: { children: ReactNode }) => children,
  useScopeCheck: (scope?: string) =>
    scope === "org:secret:update" ? mockCanUpdate : true,
}))
jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({ workspaces: mockWorkspaces }),
}))
jest.mock("@/hooks/use-secret-stores", () => ({
  useOrgSecretStores: () => ({
    stores: mockStores,
    isLoading: false,
    error: null,
    createStore: mockCreateStore,
    createStorePending: false,
    updateStore: mockUpdateStore,
  }),
}))

const ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"

function draftStore(overrides: Partial<SecretStoreRead> = {}): SecretStoreRead {
  return {
    id: "store-1",
    organization_id: "org-1",
    name: "prod",
    provider: "aws_secrets_manager",
    config: { region: "us-east-1", external_id: "tracecat-test-external-id" },
    enabled: false,
    all_workspaces: false,
    tracecat_aws_principal_arn:
      "arn:aws:iam::210987654321:role/tracecat-executor",
    created_at: "2026-10-02T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...overrides,
  }
}

function enabledStore(overrides: Partial<SecretStoreRead> = {}) {
  return draftStore({
    enabled: true,
    config: {
      role_arn: ROLE_ARN,
      region: "us-east-1",
      external_id: "tracecat-test-external-id",
    },
    ...overrides,
  })
}

function renderSettings() {
  return render(
    <TooltipProvider>
      <OrgSettingsSecretStores />
    </TooltipProvider>
  )
}

async function openMenu(name: string) {
  const user = userEvent.setup()
  screen.getByRole("button", { name }).focus()
  await user.keyboard("{Enter}")
  return user
}

async function openCreateDialog() {
  const user = await openMenu("Add store")
  await user.click(
    screen.getByRole("menuitem", { name: /AWS Secrets Manager/ })
  )
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "prod" },
  })
  return user
}

beforeEach(() => {
  mockStores = []
  mockCanUpdate = true
  mockWorkspaces = []
  mockCreateStore.mockReset()
  mockUpdateStore.mockReset()
})

describe("CreateSecretStoreDialog", () => {
  it("asks only for name and region before the trust policy exists", async () => {
    renderSettings()
    await openCreateDialog()

    expect(screen.queryByLabelText("Role ARN")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "US East" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(
      await screen.findByText(/Enter a region code such as us-east-1/)
    ).toBeInTheDocument()
    expect(mockCreateStore).not.toHaveBeenCalled()
  })

  it("saves the store disabled, then shows the policies and enables it with the role", async () => {
    mockCreateStore.mockImplementation(async () => {
      const store = draftStore()
      mockStores = [store]
      return store
    })
    mockUpdateStore.mockResolvedValue(undefined)
    renderSettings()
    await openCreateDialog()
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "us-east-1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(await screen.findByLabelText("Trust policy")).toHaveTextContent(
      "tracecat-test-external-id"
    )
    expect(screen.getByRole("dialog")).toHaveTextContent("Connect the AWS role")
    expect(mockCreateStore).toHaveBeenCalledWith({
      name: "prod",
      config: { provider: "aws_secrets_manager", region: "us-east-1" },
      enabled: false,
      all_workspaces: false,
    })

    fireEvent.change(screen.getByLabelText("Role ARN"), {
      target: { value: "arn:aws:iam::{Account}:role/{RoleNameWithPath}" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and enable" }))
    expect(
      await screen.findByText(/Replace the placeholders such as \{Account\}/)
    ).toBeInTheDocument()
    expect(mockUpdateStore).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText("Role ARN"), {
      target: { value: ROLE_ARN },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and enable" }))
    await waitFor(() =>
      expect(mockUpdateStore).toHaveBeenCalledWith({
        storeId: "store-1",
        params: { config: { role_arn: ROLE_ARN }, enabled: true },
      })
    )
  })

  it("tells create-only users who can finish setup instead of offering to enable", async () => {
    mockCanUpdate = false
    mockCreateStore.mockImplementation(async () => {
      const store = draftStore()
      mockStores = [store]
      return store
    })
    renderSettings()
    await openCreateDialog()
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "us-east-1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(
      await screen.findByText(/permission to update secret stores/)
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Role ARN")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Save and enable" })
    ).not.toBeInTheDocument()
  })
})

describe("Store setup dialog", () => {
  it("shows neither the role field nor the permission notice while scopes load", () => {
    mockCanUpdate = undefined
    mockStores = [draftStore()]
    renderSettings()
    fireEvent.click(screen.getByRole("button", { name: "prod" }))

    const dialog = screen.getByRole("dialog")
    expect(within(dialog).queryByLabelText("Role ARN")).not.toBeInTheDocument()
    expect(
      within(dialog).queryByText(/permission to update secret stores/)
    ).not.toBeInTheDocument()
    expect(
      within(dialog).queryByRole("button", { name: "Save and enable" })
    ).not.toBeInTheDocument()
  })
})

describe("Store list", () => {
  it("groups stores under their provider", () => {
    mockStores = [enabledStore(), draftStore({ id: "store-2", name: "eu" })]
    renderSettings()

    expect(
      screen.getByRole("region", { name: "AWS Secrets Manager stores" })
    ).toHaveTextContent("2 stores")
  })

  it("opens the setup flow when a store in setup is clicked", () => {
    mockStores = [draftStore()]
    renderSettings()

    expect(screen.getByText("Setup incomplete")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Finish setup" })
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "prod" }))

    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByLabelText("Role ARN")).toBeInTheDocument()
    expect(within(dialog).getByLabelText("Trust policy")).toHaveTextContent(
      "tracecat-test-external-id"
    )
  })

  it("switches between the trust and permissions policies", async () => {
    mockStores = [enabledStore()]
    const user = userEvent.setup()
    renderSettings()
    await user.click(screen.getByRole("button", { name: "prod" }))

    expect(screen.getByLabelText("Trust policy")).toHaveTextContent(
      "tracecat-test-external-id"
    )
    await user.click(screen.getByRole("tab", { name: "Permissions policy" }))
    expect(screen.getByLabelText("Permissions policy")).toHaveTextContent(
      "secretsmanager:GetSecretValue"
    )
    expect(screen.getByLabelText("Trust policy")).toBeEmptyDOMElement()
    expect(
      screen.getByRole("button", { name: "Copy permissions policy" })
    ).toHaveTextContent("Copy")
  })

  it("offers finish setup instead of edit while setup is incomplete", async () => {
    mockStores = [draftStore()]
    renderSettings()

    await openMenu("Actions for prod")
    expect(
      screen.getByRole("menuitem", { name: "Finish setup" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("menuitem", { name: "Edit store" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("menuitem", { name: /Enable store|Disable store/ })
    ).not.toBeInTheDocument()
  })

  it("asks before disabling a store that secrets use", async () => {
    mockStores = [enabledStore({ reference_count: 2 })]
    mockUpdateStore.mockImplementation(
      async ({ params }: { params: SecretStoreUpdate }) => {
        Object.assign(mockStores[0], params)
      }
    )
    renderSettings()
    expect(screen.getByText("Enabled")).toBeInTheDocument()

    const user = await openMenu("Actions for prod")
    await user.click(screen.getByRole("menuitem", { name: "Disable store" }))
    expect(
      screen.getByText(/2 secrets stop resolving in every workflow/)
    ).toBeInTheDocument()
    expect(mockUpdateStore).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Disable store" }))

    expect(mockUpdateStore).toHaveBeenCalledWith({
      storeId: "store-1",
      params: { enabled: false },
    })
  })
})

describe("Used by", () => {
  it("shows the top workspaces inline and searches the rest", async () => {
    mockWorkspaces = ["Alpha", "Bravo", "Charlie", "Delta"].map((name) => ({
      id: name.toLowerCase(),
      name,
    }))
    mockStores = [
      enabledStore({
        reference_count: 10,
        workspace_usage: [
          { workspace_id: "alpha", secret_count: 4 },
          { workspace_id: "bravo", secret_count: 3 },
          { workspace_id: "charlie", secret_count: 2 },
          { workspace_id: "delta", secret_count: 1 },
        ],
      }),
    ]
    const user = userEvent.setup()
    renderSettings()
    await user.click(screen.getByRole("button", { name: "prod" }))

    expect(screen.getByText("10 secrets in 4 workspaces")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "4 secrets" })).toHaveAttribute(
      "href",
      "/workspaces/alpha/credentials"
    )
    expect(screen.queryByText("Delta")).not.toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "View all 4 workspaces" })
    )
    await user.type(
      screen.getByLabelText("Search workspaces using this store"),
      "del"
    )
    expect(screen.getByRole("link", { name: /Delta/ })).toHaveAttribute(
      "href",
      "/workspaces/delta/credentials"
    )
    expect(screen.getAllByText("Alpha")).toHaveLength(1)
  })

  it("links only workspaces the user can open", async () => {
    mockWorkspaces = [{ id: "alpha", name: "Alpha" }]
    mockStores = [
      enabledStore({
        reference_count: 5,
        workspace_usage: [
          { workspace_id: "alpha", secret_count: 3 },
          { workspace_id: "hidden", secret_count: 2 },
        ],
      }),
    ]
    const user = userEvent.setup()
    renderSettings()
    await user.click(screen.getByRole("button", { name: "prod" }))

    expect(screen.getByRole("link", { name: "3 secrets" })).toHaveAttribute(
      "href",
      "/workspaces/alpha/credentials"
    )
    expect(screen.getByText("Unknown workspace")).toBeInTheDocument()
    expect(screen.getByText("2 secrets")).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "2 secrets" })
    ).not.toBeInTheDocument()
  })

  it("says when no secrets use the store", async () => {
    mockStores = [enabledStore()]
    const user = userEvent.setup()
    renderSettings()
    await user.click(screen.getByRole("button", { name: "prod" }))

    expect(screen.getByText("No secrets yet")).toBeInTheDocument()
  })
})

describe("Workspace picker", () => {
  it("filters workspaces by name", async () => {
    const user = userEvent.setup()
    mockStores = [draftStore()]
    mockWorkspaces = [
      { id: "ws-1", name: "Detection Engineering" },
      { id: "ws-2", name: "Incident Response" },
    ]
    renderSettings()

    await user.click(
      screen.getByRole("button", { name: "Workspaces: Select workspaces" })
    )
    await user.type(screen.getByLabelText("Search workspaces"), "incid")

    expect(screen.getByText("Incident Response")).toBeInTheDocument()
    expect(screen.queryByText("Detection Engineering")).not.toBeInTheDocument()

    await user.clear(screen.getByLabelText("Search workspaces"))
    await user.type(screen.getByLabelText("Search workspaces"), "zzz")
    expect(screen.getByText("No workspaces match.")).toBeInTheDocument()
  })
})
