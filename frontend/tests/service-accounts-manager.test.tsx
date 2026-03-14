/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type {
  ServiceAccountApiKeyIssueResponse,
  ServiceAccountApiKeyRead,
  ServiceAccountRead,
  ServiceAccountScopeRead,
} from "@/client"
import { ServiceAccountsManager } from "@/components/organization/service-accounts-manager"

jest.mock("@/components/cases/case-panel-section", () => ({
  CasePanelSection: ({
    title,
    children,
  }: {
    title: string
    isOpen: boolean
    onOpenChange: (open: boolean) => void
    children: ReactNode
  }) => (
    <section>
      <h3>{title}</h3>
      <div>{children}</div>
    </section>
  ),
}))

jest.mock("@/components/copy-button", () => ({
  CopyButton: () => <button type="button">Copy</button>,
}))

jest.mock("@/components/rbac/scope-category-row", () => ({
  getScopesForLevel: () => [],
  ScopeCategoryRow: () => <div>Scope category</div>,
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({
    open,
    children,
  }: {
    open: boolean
    onOpenChange?: (open: boolean) => void
    children: ReactNode
  }) => (open ? <div>{children}</div> : null),
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

jest.mock("@/components/ui/alert-dialog", () => ({
  AlertDialog: ({
    open,
    children,
  }: {
    open: boolean
    onOpenChange?: (open: boolean) => void
    children: ReactNode
  }) => (open ? <div>{children}</div> : null),
  AlertDialogAction: ({
    children,
    onClick,
  }: {
    children: ReactNode
    disabled?: boolean
    onClick?: () => void
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  AlertDialogCancel: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  AlertDialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogTitle: ({ children }: { children: ReactNode }) => (
    <h2>{children}</h2>
  ),
}))

jest.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockListApiKeys = jest.fn<
  Promise<{
    items?: ServiceAccountApiKeyRead[]
    next_cursor?: string | null
  }>,
  [string]
>()

function createServiceAccount(params: {
  id: string
  name: string
  description?: string | null
  disabledAt?: string | null
  activeApiKey?: ServiceAccountApiKeyRead | null
  totalKeys: number
  scopes?: ServiceAccountScopeRead[]
}): ServiceAccountRead {
  return {
    id: params.id,
    organization_id: "org-1",
    workspace_id: "workspace-1",
    owner_user_id: null,
    name: params.name,
    description: params.description ?? null,
    disabled_at: params.disabledAt ?? null,
    last_used_at: "2024-02-01T00:00:00Z",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    scopes: params.scopes ?? [],
    active_api_key: params.activeApiKey ?? null,
    api_key_counts: {
      total: params.totalKeys,
      active: params.activeApiKey ? 1 : 0,
      revoked: params.activeApiKey ? params.totalKeys - 1 : params.totalKeys,
    },
  }
}

function createIssuedResponse(
  serviceAccount: ServiceAccountRead
): ServiceAccountApiKeyIssueResponse {
  return {
    service_account: serviceAccount,
    issued_api_key: {
      raw_key: "tc_ws_sk_raw_key",
      api_key: {
        id: "issued-key",
        name: "Primary",
        key_id: "key-id-issued",
        preview: "tc_ws_sk_...1234",
        created_by: null,
        revoked_by: null,
        last_used_at: null,
        revoked_at: null,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    },
  }
}

function renderManager(serviceAccounts: ServiceAccountRead[]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ServiceAccountsManager
        kindLabel="Workspace"
        serviceAccounts={serviceAccounts}
        nextCursor={null}
        isLoading={false}
        error={null}
        availableScopes={[]}
        createPending={false}
        updatePending={false}
        disablePending={false}
        enablePending={false}
        issueApiKeyPending={false}
        revokeApiKeyPending={false}
        apiKeysQueryKeyPrefix={["workspace-service-accounts", "workspace-1"]}
        onCreate={async (requestBody) =>
          createIssuedResponse(
            createServiceAccount({
              id: "created-account",
              name: requestBody.name,
              totalKeys: 1,
            })
          )
        }
        onUpdate={async ({ serviceAccountId, requestBody }) => {
          const existingServiceAccount = serviceAccounts.find(
            (serviceAccount) => serviceAccount.id === serviceAccountId
          )
          if (!existingServiceAccount) {
            throw new Error("Expected service account fixture")
          }

          return {
            ...existingServiceAccount,
            name: requestBody.name ?? existingServiceAccount.name,
            description:
              requestBody.description === undefined
                ? existingServiceAccount.description
                : requestBody.description,
          }
        }}
        onDisable={async () => {}}
        onEnable={async () => {}}
        onIssueApiKey={async ({ serviceAccountId }) =>
          createIssuedResponse(
            serviceAccounts.find(
              (serviceAccount) => serviceAccount.id === serviceAccountId
            )!
          )
        }
        onRevokeApiKey={async () => {}}
        listApiKeys={mockListApiKeys}
      />
    </QueryClientProvider>
  )
}

describe("ServiceAccountsManager", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListApiKeys.mockResolvedValue({
      items: [
        {
          id: "key-1",
          name: "Primary",
          key_id: "key-id-1",
          preview: "tc_ws_sk_...1234",
          created_by: null,
          revoked_by: null,
          last_used_at: null,
          revoked_at: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      next_cursor: null,
    })
  })

  it("shows service accounts as top-level rows, keeps status filters, and expands into API keys", async () => {
    const activeKey: ServiceAccountApiKeyRead = {
      id: "active-key",
      name: "Primary",
      key_id: "key-id-active",
      preview: "tc_ws_sk_...5678",
      created_by: null,
      revoked_by: null,
      last_used_at: null,
      revoked_at: null,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    }
    const serviceAccounts = [
      createServiceAccount({
        id: "sa-active",
        name: "Alpha bot",
        description: "CI deploy access",
        activeApiKey: activeKey,
        totalKeys: 2,
      }),
      createServiceAccount({
        id: "sa-disabled",
        name: "Bravo bot",
        description: "Disabled account",
        disabledAt: "2024-02-15T00:00:00Z",
        totalKeys: 1,
      }),
      createServiceAccount({
        id: "sa-no-key",
        name: "Charlie bot",
        description: "Awaiting issuance",
        totalKeys: 0,
      }),
    ]

    renderManager(serviceAccounts)

    expect(screen.getByText("Alpha bot")).toBeInTheDocument()
    expect(screen.getByText("Bravo bot")).toBeInTheDocument()
    expect(screen.getByText("Charlie bot")).toBeInTheDocument()
    expect(screen.getAllByText("Active").length).toBeGreaterThan(1)
    expect(screen.getAllByText("Disabled").length).toBeGreaterThan(1)
    expect(screen.getAllByText("No active key").length).toBeGreaterThan(1)
    expect(mockListApiKeys).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Disabled" }))

    expect(screen.queryByText("Alpha bot")).not.toBeInTheDocument()
    expect(screen.getByText("Bravo bot")).toBeInTheDocument()
    expect(screen.queryByText("Charlie bot")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "All" }))
    fireEvent.click(screen.getByRole("button", { name: /Alpha bot/i }))

    await waitFor(() => {
      expect(mockListApiKeys).toHaveBeenCalledWith("sa-active")
    })
    expect(screen.getByText("API keys")).toBeInTheDocument()
    expect(screen.getByText("Scopes")).toBeInTheDocument()
    expect(
      await screen.findByRole("button", { name: "Revoke API key" })
    ).toBeInTheDocument()
  })
})
