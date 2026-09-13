import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

function settingsWithEndpoint(endpoint: string) {
  return {
    agent_otel_config: {
      enabled: true,
      endpoint,
      metrics_enabled: true,
      logs_enabled: true,
      traces_enabled: false,
    },
  }
}

function mockSettingsHook(
  overrides: Partial<ReturnType<typeof useOrgAgentOtelSettings>> = {}
) {
  jest.mocked(useOrgAgentOtelSettings).mockReturnValue({
    agentOtelSettings: settingsWithEndpoint("https://collector.example.com"),
    agentOtelSettingsIsLoading: false,
    agentOtelSettingsError: null,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending: false,
    updateAgentOtelSettingsError: null,
    ...overrides,
  } as ReturnType<typeof useOrgAgentOtelSettings>)
}

beforeEach(() => {
  updateAgentOtelSettings.mockClear()
})

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

  it("stays editable when a background refetch fails with retained data", async () => {
    mockSettingsHook({
      agentOtelSettingsError: new Error("Request failed"),
    })

    render(<OrgAgentOtelSettings />)

    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save config" })).toBeEnabled()
    )
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
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "Enable agent telemetry" })
      ).toBeChecked()
    )
    expect(
      screen.getByRole("switch", { name: "Enable agent telemetry" })
    ).toBeDisabled()
    expect(screen.getByRole("button", { name: "Save config" })).toBeDisabled()
  })

  it("keeps controls editable with loaded settings and the update scope", async () => {
    render(<OrgAgentOtelSettings />)

    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "Enable agent telemetry" })
      ).toBeChecked()
    )
    expect(
      screen.getByRole("switch", { name: "Enable agent telemetry" })
    ).toBeEnabled()
    expect(screen.getByRole("button", { name: "Save config" })).toBeEnabled()
  })
})

describe("OrgAgentOtelSettings server resync", () => {
  beforeEach(() => {
    jest.mocked(useScopeCheck).mockReturnValue(true)
    mockSettingsHook({
      agentOtelSettings: settingsWithEndpoint("old-collector"),
    })
  })

  it("adopts a changed server endpoint while the form is pristine", async () => {
    const { rerender } = render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await waitFor(() => expect(endpoint).toHaveValue("old-collector"))

    mockSettingsHook({
      agentOtelSettings: settingsWithEndpoint("new-collector"),
    })
    rerender(<OrgAgentOtelSettings />)

    await waitFor(() => expect(endpoint).toHaveValue("new-collector"))
  })

  it("keeps unsaved edits when the server config changes", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await waitFor(() => expect(endpoint).toHaveValue("old-collector"))

    await user.clear(endpoint)
    await user.type(endpoint, "my-draft")
    expect(endpoint).toHaveValue("my-draft")

    mockSettingsHook({
      agentOtelSettings: settingsWithEndpoint("new-collector"),
    })
    rerender(<OrgAgentOtelSettings />)

    await waitFor(() => expect(endpoint).toHaveValue("my-draft"))
    expect(screen.getByRole("button", { name: "Reset" })).toBeEnabled()
  })

  it("disables Reset until the form is edited", async () => {
    render(<OrgAgentOtelSettings />)

    const reset = screen.getByRole("button", { name: "Reset" })
    await waitFor(() => expect(reset).toBeDisabled())

    await userEvent
      .setup()
      .type(screen.getByLabelText("Collector endpoint"), "x")
    expect(reset).toBeEnabled()
  })
})

describe("OrgAgentOtelSettings collector header origin binding", () => {
  beforeEach(() => {
    jest.mocked(useScopeCheck).mockReturnValue(true)
    mockSettingsHook()
  })

  it("clears retained headers when the collector origin changes", async () => {
    const user = userEvent.setup()
    render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await waitFor(() =>
      expect(endpoint).toHaveValue("https://collector.example.com")
    )
    await user.clear(endpoint)
    await user.type(endpoint, "https://other.example.com/otel")
    await user.tab()

    expect(
      screen.getByText(/Saved collector headers will be cleared/)
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save config" }))

    await waitFor(() =>
      expect(updateAgentOtelSettings).toHaveBeenCalledTimes(1)
    )
    expect(updateAgentOtelSettings).toHaveBeenCalledWith({
      requestBody: expect.objectContaining({
        agent_otel_headers: {},
      }),
    })
  })

  it.each([
    [
      "changes only path, case, and default port",
      "https://collector.example.com",
      "https://COLLECTOR.example.com:443/otel",
    ],
    [
      "adds a DNS root dot",
      "https://collector.example.com/first",
      "https://COLLECTOR.example.com.:443/second",
    ],
    [
      "removes a DNS root dot",
      "https://collector.example.com./first",
      "https://COLLECTOR.example.com:443/second",
    ],
  ])(
    "preserves retained headers when a same-origin edit %s",
    async (_scenario, savedEndpoint, nextEndpoint) => {
      mockSettingsHook({
        agentOtelSettings: settingsWithEndpoint(savedEndpoint),
      })
      const user = userEvent.setup()
      render(<OrgAgentOtelSettings />)

      const endpoint = screen.getByLabelText("Collector endpoint")
      await waitFor(() => expect(endpoint).toHaveValue(savedEndpoint))
      await user.clear(endpoint)
      await user.type(endpoint, nextEndpoint)
      await user.tab()
      expect(
        screen.queryByText(/Saved collector headers will be cleared/)
      ).not.toBeInTheDocument()
      await user.click(screen.getByRole("button", { name: "Save config" }))

      await waitFor(() =>
        expect(updateAgentOtelSettings).toHaveBeenCalledTimes(1)
      )
      expect(updateAgentOtelSettings).toHaveBeenCalledWith({
        requestBody: expect.objectContaining({
          agent_otel_headers: undefined,
        }),
      })
    }
  )

  it("accepts replacement headers after an origin change", async () => {
    const user = userEvent.setup()
    render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await waitFor(() =>
      expect(endpoint).toHaveValue("https://collector.example.com")
    )
    await user.clear(endpoint)
    await user.type(endpoint, "https://other.example.com/otel")
    await user.tab()
    await user.click(screen.getByRole("button", { name: "Add header" }))
    await user.type(screen.getByPlaceholderText("Header name"), "Authorization")
    await user.type(
      screen.getByPlaceholderText("Header value"),
      "Bearer replacement"
    )
    await user.click(screen.getByRole("button", { name: "Save config" }))

    await waitFor(() =>
      expect(updateAgentOtelSettings).toHaveBeenCalledTimes(1)
    )
    expect(updateAgentOtelSettings).toHaveBeenCalledWith({
      requestBody: expect.objectContaining({
        agent_otel_headers: { Authorization: "Bearer replacement" },
      }),
    })
  })
})

describe("OrgAgentOtelSettings save reseed guard", () => {
  beforeEach(() => {
    jest.mocked(useScopeCheck).mockReturnValue(true)
  })

  it("keeps submitted values pristine when the post-save refetch fails", async () => {
    // A failed refetch leaves pre-save data in the cache; its sig matches the
    // pre-save baseline, so the resync effect must not reseed over the save.
    const user = userEvent.setup()
    render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await user.clear(endpoint)
    await user.type(endpoint, "https://new.example.com")
    await user.click(screen.getByRole("button", { name: "Save config" }))

    await waitFor(() => expect(updateAgentOtelSettings).toHaveBeenCalled())
    expect(endpoint).toHaveValue("https://new.example.com")
    expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled()
  })

  it("adopts the canonical post-save read when the refetch lands", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<OrgAgentOtelSettings />)

    const endpoint = screen.getByLabelText("Collector endpoint")
    await user.clear(endpoint)
    await user.type(endpoint, "https://new.example.com")
    await user.click(screen.getByRole("button", { name: "Save config" }))
    await waitFor(() => expect(updateAgentOtelSettings).toHaveBeenCalled())

    mockSettingsHook({
      agentOtelSettings: settingsWithEndpoint("https://new.example.com/"),
    })
    rerender(<OrgAgentOtelSettings />)

    await waitFor(() =>
      expect(endpoint).toHaveValue("https://new.example.com/")
    )
  })
})
