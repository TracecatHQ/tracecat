import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { SecretStoreRead } from "@/client"
import { OrgSettingsSecretStores } from "@/components/organization/org-settings-secret-stores"
import { TooltipProvider } from "@/components/ui/tooltip"

const mockCreateStore = jest.fn()
const mockUpdateStore = jest.fn()
let mockStores: SecretStoreRead[] = []
let mockCanUpdate = true
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

function renderSettings() {
  return render(
    <TooltipProvider>
      <OrgSettingsSecretStores />
    </TooltipProvider>
  )
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
    fireEvent.click(screen.getByRole("button", { name: /Add store/ }))

    expect(screen.queryByLabelText("Role ARN")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "prod" },
    })
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
    mockCreateStore.mockResolvedValue(draftStore())
    mockUpdateStore.mockResolvedValue(undefined)
    renderSettings()
    fireEvent.click(screen.getByRole("button", { name: /Add store/ }))

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "prod" },
    })
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "us-east-1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    const trustPolicy =
      await screen.findByLabelText<HTMLTextAreaElement>("Trust policy")
    expect(trustPolicy.value).toContain("tracecat-test-external-id")
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
})

describe("SecretStoreCard", () => {
  it("shows a finish-setup action instead of the enabled switch until the role is set", () => {
    mockStores = [draftStore()]
    renderSettings()

    expect(screen.getByText("Setup incomplete")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Finish setup" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Disabled")).not.toBeInTheDocument()
  })

  it("shows the enabled switch once setup is complete", () => {
    mockStores = [
      draftStore({
        enabled: true,
        config: {
          role_arn: ROLE_ARN,
          region: "us-east-1",
          external_id: "tracecat-test-external-id",
        },
      }),
    ]
    renderSettings()

    expect(screen.queryByText("Setup incomplete")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Enabled")).toBeChecked()
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

describe("StoreSetupDialogContent", () => {
  it("tells create-only users who can finish setup instead of offering to enable", async () => {
    mockCanUpdate = false
    mockCreateStore.mockResolvedValue(draftStore())
    renderSettings()
    fireEvent.click(screen.getByRole("button", { name: /Add store/ }))
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "prod" },
    })
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
