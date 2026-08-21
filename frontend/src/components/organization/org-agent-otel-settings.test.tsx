import { render, screen, waitFor } from "@testing-library/react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { OrgAgentOtelSettings } from "@/components/organization/org-agent-otel-settings"
import { useOrgAgentOtelSettings } from "@/hooks/use-org-agent-otel-settings"

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/hooks/use-org-agent-otel-settings", () => ({
  useOrgAgentOtelSettings: jest.fn(),
}))

const updateAgentOtelSettings = jest.fn(async () => undefined)

function mockSettingsHook(
  overrides: Partial<ReturnType<typeof useOrgAgentOtelSettings>> = {}
) {
  jest.mocked(useOrgAgentOtelSettings).mockReturnValue({
    agentOtelSettings: {
      agent_otel_config: {
        enabled: true,
        endpoint: "https://collector.example.com",
        metrics_enabled: true,
        logs_enabled: true,
        traces_enabled: false,
      },
    },
    agentOtelSettingsIsLoading: false,
    agentOtelSettingsError: null,
    updateAgentOtelSettings,
    getLatestAgentOtelSettings: jest.fn(() => undefined),
    updateAgentOtelSettingsIsPending: false,
    updateAgentOtelSettingsError: null,
    ...overrides,
  } as ReturnType<typeof useOrgAgentOtelSettings>)
}

describe("OrgAgentOtelSettings edit gating", () => {
  beforeEach(() => {
    jest.mocked(useScopeCheck).mockReturnValue(true)
    mockSettingsHook()
  })

  it("disables saving when settings fail to load", () => {
    mockSettingsHook({
      agentOtelSettings: undefined,
      agentOtelSettingsError: new Error("Request failed"),
    })

    render(<OrgAgentOtelSettings />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Editing is disabled to protect the saved configuration."
    )
    expect(screen.getByRole("button", { name: "Save config" })).toBeDisabled()
  })

  it("disables saving when settings return no data", () => {
    mockSettingsHook({ agentOtelSettings: undefined })

    render(<OrgAgentOtelSettings />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Editing is disabled to protect the saved configuration."
    )
    expect(screen.getByRole("button", { name: "Save config" })).toBeDisabled()
  })

  it("renders settings read-only without the update scope", async () => {
    jest.mocked(useScopeCheck).mockReturnValue(false)

    render(<OrgAgentOtelSettings />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "you do not have permission to update them"
    )
    await waitFor(() => expect(screen.getByRole("switch")).toBeChecked())
    expect(screen.getByRole("switch")).toBeDisabled()
    expect(screen.getByRole("button", { name: "Raw mode" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Save config" })).toBeDisabled()
  })

  it("keeps controls editable with loaded settings and the update scope", async () => {
    render(<OrgAgentOtelSettings />)

    await waitFor(() => expect(screen.getByRole("switch")).toBeChecked())
    expect(screen.getByRole("switch")).toBeEnabled()
    expect(screen.getByRole("button", { name: "Save config" })).toBeEnabled()
  })
})
