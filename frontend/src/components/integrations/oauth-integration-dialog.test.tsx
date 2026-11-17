import { render, screen } from "@testing-library/react"
import { OAuthIntegrationDialog } from "@/components/integrations/oauth-integration-dialog"
import { TooltipProvider } from "@/components/ui/tooltip"

const REDIRECT_URI = "https://tracecat.example.com/integrations/callback"
let mockProvider: Record<string, unknown> | undefined

jest.mock("@/components/icons", () => ({
  ProviderIcon: () => null,
}))
jest.mock("@/components/provider-config-form", () => ({
  ProviderConfigForm: () => <div data-testid="provider-config-form" />,
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/lib/hooks", () => ({
  useIntegrationProvider: () => ({
    provider: mockProvider,
    providerIsLoading: false,
    providerError: null,
    integration: null,
    connectProvider: jest.fn(),
    connectProviderIsPending: false,
    testConnection: jest.fn(),
    testConnectionIsPending: false,
  }),
}))

function provider(overrides: Record<string, unknown>) {
  return {
    id: "microsoft_graph",
    grant_type: "authorization_code",
    redirect_uri: REDIRECT_URI,
    metadata: {
      id: "microsoft_graph",
      name: "Microsoft Graph",
      description: "Microsoft Graph API",
      requires_config: true,
      enabled: true,
    },
    ...overrides,
  }
}

function renderDialog(grantType: "authorization_code" | "client_credentials") {
  return render(
    <TooltipProvider>
      <OAuthIntegrationDialog
        providerId="microsoft_graph"
        grantType={grantType}
        open
        onOpenChange={jest.fn()}
      />
    </TooltipProvider>
  )
}

describe("OAuthIntegrationDialog", () => {
  it("shows the redirect URI for authorization code providers", () => {
    mockProvider = provider({})
    renderDialog("authorization_code")

    expect(screen.getByLabelText("Redirect URI")).toHaveValue(REDIRECT_URI)
    expect(
      screen.getByRole("button", { name: "Copy redirect uri" })
    ).toBeInTheDocument()
  })

  it("hides the redirect URI for client credentials providers", () => {
    mockProvider = provider({
      grant_type: "client_credentials",
      redirect_uri: null,
    })
    renderDialog("client_credentials")

    expect(screen.queryByLabelText("Redirect URI")).not.toBeInTheDocument()
    expect(screen.getByTestId("provider-config-form")).toBeInTheDocument()
  })
})
