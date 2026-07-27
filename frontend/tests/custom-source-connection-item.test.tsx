import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentCustomProviderRead } from "@/client"
import {
  type CustomSourceCard,
  CustomSourceConnectionItem,
} from "@/components/organization/org-settings-agent"

jest.mock("@/components/icons", () => ({
  ProviderIcon: ({ providerId }: { providerId: string }) => (
    <span data-testid={`icon-${providerId}`} />
  ),
}))

const provider: AgentCustomProviderRead = {
  id: "prov-1",
  organization_id: "org-1",
  display_name: "Local gateway",
  base_url: "https://gw.example.com/v1",
  type: "generic_openai_compatible",
  passthrough: false,
  api_key_header: null,
  last_refreshed_at: null,
}

function buildSource(
  overrides: Partial<CustomSourceCard> = {}
): CustomSourceCard {
  return {
    id: provider.id,
    type: "openai_compatible_gateway",
    flavor: null,
    display_name: provider.display_name,
    base_url: provider.base_url,
    api_key_configured: false,
    api_key_header: provider.api_key_header,
    discovery_status: "loaded",
    last_refreshed_at: null,
    last_error: null,
    provider,
    models: [
      {
        id: "cat-1",
        source_id: provider.id,
        source_name: provider.display_name,
        source_type: "openai_compatible_gateway",
        model_provider: "custom-model-provider",
        model_name: "gw-model-a",
        metadata: {},
        base_url: provider.base_url,
        enabled: true,
      },
      {
        id: "cat-2",
        source_id: provider.id,
        source_name: provider.display_name,
        source_type: "openai_compatible_gateway",
        model_provider: "custom-model-provider",
        model_name: "gw-model-b",
        metadata: {},
        base_url: provider.base_url,
        enabled: false,
      },
    ],
    ...overrides,
  }
}

function renderItem(source: CustomSourceCard) {
  return render(
    <CustomSourceConnectionItem
      actionsDisabled={false}
      agentAddonsEnabled={true}
      isExpanded={true}
      modelsDisabled={false}
      onDelete={jest.fn()}
      onEdit={jest.fn()}
      onExpandedChange={jest.fn()}
      onRefresh={jest.fn()}
      onToggleModel={jest.fn()}
      source={source}
    />
  )
}

describe("CustomSourceConnectionItem", () => {
  it("renders the enabled-count pill with the condensed model summary", () => {
    renderItem(buildSource())
    expect(screen.getByText("1 enabled")).toBeInTheDocument()
  })

  it("renders a no-models pill when the source has no discovered models", () => {
    renderItem(buildSource({ models: [] }))
    expect(screen.getByText("No models discovered")).toBeInTheDocument()
  })

  it("shows a Passthrough pill when passthrough is enabled", () => {
    renderItem(buildSource({ provider: { ...provider, passthrough: true } }))
    expect(screen.getByText("Passthrough")).toBeInTheDocument()
  })

  it("omits the Passthrough pill when passthrough is disabled", () => {
    renderItem(buildSource())
    expect(screen.queryByText("Passthrough")).not.toBeInTheDocument()
  })

  it("drops the provider slug, Custom URL chip, and source subtitle", () => {
    renderItem(buildSource())
    expect(screen.queryByText("custom-model-provider")).not.toBeInTheDocument()
    expect(screen.queryByText("Custom URL")).not.toBeInTheDocument()
    // The provider display name still appears in the header/menu, but not as a
    // per-row subtitle; the model rows show only their own name.
    expect(screen.getByText("gw-model-a")).toBeInTheDocument()
  })

  it("exposes the provider name and provider-level actions via the menu", async () => {
    const user = userEvent.setup()
    renderItem(buildSource())
    expect(screen.getAllByText("Local gateway").length).toBeGreaterThan(0)

    await user.click(screen.getByRole("button", { name: "Source actions" }))

    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: "Refresh" })
    ).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument()
  })
})
