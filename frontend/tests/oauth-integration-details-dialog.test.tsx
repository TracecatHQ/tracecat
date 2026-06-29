/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import type { IntegrationRead, OAuthGrantType, ProviderRead } from "@/client"
import { OAuthIntegrationDetailsDialog } from "@/components/integrations/oauth-integration-details-dialog"
import {
  useConnectProvider,
  useDisconnectProvider,
  useTestProvider,
} from "@/hooks/use-integration-actions"
import { useIntegrationProvider } from "@/lib/hooks"

jest.mock("@/components/confirm-destructive-dialog", () => ({
  ConfirmDestructiveDialog: () => null,
}))

jest.mock("@/components/icons", () => ({
  ProviderIcon: () => <span data-testid="provider-icon" />,
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
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

jest.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    disabled,
    onClick,
  }: {
    children: ReactNode
    disabled?: boolean
    onClick?: () => void
  }) => (
    <button type="button" disabled={disabled} onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

jest.mock("@/hooks/use-integration-actions", () => ({
  useConnectProvider: jest.fn(),
  useDisconnectProvider: jest.fn(),
  useTestProvider: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useIntegrationProvider: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const mockUseIntegrationProvider =
  useIntegrationProvider as jest.MockedFunction<typeof useIntegrationProvider>
const mockUseConnectProvider = useConnectProvider as jest.MockedFunction<
  typeof useConnectProvider
>
const mockUseDisconnectProvider = useDisconnectProvider as jest.MockedFunction<
  typeof useDisconnectProvider
>
const mockUseTestProvider = useTestProvider as jest.MockedFunction<
  typeof useTestProvider
>

const provider: ProviderRead = {
  grant_type: "authorization_code",
  metadata: {
    id: "slack",
    name: "Slack",
    description: "Slack OAuth provider",
  },
  scopes: { default: [] },
  config_schema: { json_schema: {} },
  integration_status: "connected",
  default_authorization_endpoint: null,
  default_token_endpoint: null,
}

const integration: IntegrationRead = {
  id: "integration-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  user_id: "user-1",
  provider_id: "slack",
  authorization_endpoint: null,
  token_endpoint: null,
  token_type: "Bearer",
  expires_at: null,
  client_id: "client-id",
  granted_scopes: [],
  requested_scopes: [],
  status: "connected",
  is_expired: false,
}

function setupMocks(grantType: OAuthGrantType) {
  mockUseIntegrationProvider.mockReturnValue({
    provider: { ...provider, grant_type: grantType },
    providerIsLoading: false,
    providerError: null,
    integration,
    integrationIsLoading: false,
    integrationError: null,
  } as unknown as ReturnType<typeof useIntegrationProvider>)

  const mutation = {
    isPending: false,
    mutate: jest.fn(),
    mutateAsync: jest.fn(),
  }

  mockUseConnectProvider.mockReturnValue(
    mutation as unknown as ReturnType<typeof useConnectProvider>
  )
  mockUseDisconnectProvider.mockReturnValue(
    mutation as unknown as ReturnType<typeof useDisconnectProvider>
  )
  mockUseTestProvider.mockReturnValue(
    mutation as unknown as ReturnType<typeof useTestProvider>
  )
}

function renderDialog(grantType: OAuthGrantType) {
  setupMocks(grantType)

  render(
    <OAuthIntegrationDetailsDialog
      providerId="slack"
      grantType={grantType}
      open={true}
      onOpenChange={() => {}}
      canUpdate={true}
    />
  )
}

describe("OAuthIntegrationDetailsDialog", () => {
  it("hides the Test action for authorization-code providers", () => {
    renderDialog("authorization_code")

    expect(
      screen.queryByRole("button", { name: /test/i })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /reauthorize/i })
    ).toBeInTheDocument()
  })

  it("shows the Test action for client-credentials providers", () => {
    renderDialog("client_credentials")

    expect(screen.getByRole("button", { name: /test/i })).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /reauthorize/i })
    ).not.toBeInTheDocument()
  })
})
