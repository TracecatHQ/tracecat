/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { IntegrationRead, ProviderRead } from "@/client"
import { ProviderConfigForm } from "@/components/provider-config-form"
import { useIntegrationProvider } from "@/lib/hooks"

jest.mock("@/components/tags-input", () => ({
  MultiTagCommandInput: () => <div data-testid="scopes-input" />,
}))

jest.mock("@/lib/hooks", () => ({
  useIntegrationProvider: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const mockUseIntegrationProvider =
  useIntegrationProvider as jest.MockedFunction<typeof useIntegrationProvider>

const githubProvider = {
  grant_type: "authorization_code",
  metadata: {
    id: "github",
    name: "GitHub (Delegated)",
    description: "GitHub delegated OAuth provider",
  },
  scopes: { default: ["repo"] },
  config_schema: { json_schema: {} },
  integration_status: "connected",
  default_authorization_endpoint: "https://github.com/login/oauth/authorize",
  default_token_endpoint: "https://github.com/login/oauth/access_token",
  default_api_base_url: "https://api.github.com",
} satisfies ProviderRead

const githubIntegration = {
  id: "integration-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  user_id: "user-1",
  provider_id: "github",
  authorization_endpoint: "https://github.example.com/login/oauth/authorize",
  token_endpoint: "https://github.example.com/login/oauth/access_token",
  api_base_url: "https://github.example.com/api/v3",
  token_type: "Bearer",
  expires_at: null,
  client_id: "client-id",
  granted_scopes: ["repo"],
  requested_scopes: ["repo"],
  status: "connected",
  is_expired: false,
} satisfies IntegrationRead

describe("ProviderConfigForm API base URL", () => {
  it("shows the configured GHES API base URL for providers that support it", () => {
    mockUseIntegrationProvider.mockReturnValue({
      integration: githubIntegration,
      integrationIsLoading: false,
      updateIntegration: jest.fn(),
      updateIntegrationIsPending: false,
    } as unknown as ReturnType<typeof useIntegrationProvider>)

    render(<ProviderConfigForm provider={githubProvider} />)

    expect(screen.getByLabelText("API base URL")).toHaveValue(
      "https://github.example.com/api/v3"
    )
  })

  it("hydrates configured endpoints after the integration query resolves", async () => {
    let result = {
      integration: null,
      integrationIsLoading: true,
      updateIntegration: jest.fn(),
      updateIntegrationIsPending: false,
    } as unknown as ReturnType<typeof useIntegrationProvider>
    mockUseIntegrationProvider.mockImplementation(() => result)

    const { rerender } = render(
      <ProviderConfigForm provider={githubProvider} />
    )
    expect(screen.queryByLabelText("API base URL")).not.toBeInTheDocument()

    result = {
      ...result,
      integration: githubIntegration,
      integrationIsLoading: false,
    }
    rerender(<ProviderConfigForm provider={githubProvider} />)

    await waitFor(() => {
      expect(screen.getByLabelText("API base URL")).toHaveValue(
        "https://github.example.com/api/v3"
      )
    })
  })

  it("submits null to restore the provider API base URL", async () => {
    const user = userEvent.setup()
    const updateIntegration = jest.fn().mockResolvedValue(undefined)
    mockUseIntegrationProvider.mockReturnValue({
      integration: githubIntegration,
      integrationIsLoading: false,
      updateIntegration,
      updateIntegrationIsPending: false,
    } as unknown as ReturnType<typeof useIntegrationProvider>)

    render(<ProviderConfigForm provider={githubProvider} />)
    await user.clear(screen.getByLabelText("API base URL"))
    await user.click(screen.getByRole("button", { name: "Save configuration" }))

    await waitFor(() => {
      expect(updateIntegration).toHaveBeenCalledWith(
        expect.objectContaining({ api_base_url: null })
      )
    })
  })

  it("does not show API base URL for providers without an API host contract", () => {
    mockUseIntegrationProvider.mockReturnValue({
      integration: null,
      integrationIsLoading: false,
      updateIntegration: jest.fn(),
      updateIntegrationIsPending: false,
    } as unknown as ReturnType<typeof useIntegrationProvider>)
    const provider = {
      ...githubProvider,
      metadata: {
        id: "slack",
        name: "Slack",
        description: "Slack OAuth provider",
      },
      default_api_base_url: null,
    } satisfies ProviderRead

    render(<ProviderConfigForm provider={provider} />)

    expect(screen.queryByLabelText("API base URL")).not.toBeInTheDocument()
  })
})
