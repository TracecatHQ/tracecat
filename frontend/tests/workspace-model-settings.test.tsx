/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type {
  AgentCatalogListResponse,
  AgentCatalogRead,
  AgentCustomProviderListResponse,
  AgentModelAccessListResponse,
  AgentModelAccessRead,
  WorkspaceRead,
} from "@/client"
import {
  disableModel,
  enableModel,
  listCatalog,
  listCustomProviders,
  listEnabledModels,
} from "@/client"
import { WorkspaceModelSettings } from "@/components/settings/workspace-model-settings"

jest.mock("@/client", () => ({
  disableModel: jest.fn(),
  enableModel: jest.fn(),
  listCatalog: jest.fn(),
  listCustomProviders: jest.fn(),
  listEnabledModels: jest.fn(),
}))

jest.mock("@/components/icons", () => ({
  ProviderIcon: ({ providerId }: { providerId: string }) => (
    <span data-provider-id={providerId} />
  ),
}))

jest.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({
    checked,
    disabled,
    onCheckedChange,
  }: {
    checked?: boolean
    disabled?: boolean
    onCheckedChange?: (checked: boolean) => void
  }) => (
    <input
      checked={checked === true}
      disabled={disabled}
      onChange={(event) => onCheckedChange?.(event.target.checked)}
      type="checkbox"
    />
  ),
}))

jest.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({
    children,
    className,
    ...props
  }: {
    children: ReactNode
    className?: string
  } & React.HTMLAttributes<HTMLDivElement>) => (
    <div className={className} {...props}>
      {children}
    </div>
  ),
}))

jest.mock("@/components/ui/switch", () => ({
  Switch: ({
    checked,
    onCheckedChange,
  }: {
    checked?: boolean
    onCheckedChange?: (checked: boolean) => void
  }) => (
    <input
      aria-label="Inherit organization-enabled models"
      checked={checked === true}
      onChange={(event) => onCheckedChange?.(event.target.checked)}
      role="switch"
      type="checkbox"
    />
  ),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const catalogEntries = [
  {
    id: "catalog-alpha",
    custom_provider_id: null,
    organization_id: null,
    model_metadata: null,
    model_name: "Alpha Model",
    model_provider: "openai",
  },
  {
    id: "catalog-beta",
    custom_provider_id: null,
    organization_id: null,
    model_metadata: null,
    model_name: "Beta Model",
    model_provider: "anthropic",
  },
] satisfies AgentCatalogRead[]

const accessRows = [
  {
    id: "org-alpha",
    catalog_id: "catalog-alpha",
    organization_id: "org-123",
    workspace_id: null,
  },
  {
    id: "org-beta",
    catalog_id: "catalog-beta",
    organization_id: "org-123",
    workspace_id: null,
  },
  {
    id: "workspace-alpha",
    catalog_id: "catalog-alpha",
    organization_id: "org-123",
    workspace_id: "workspace-123",
  },
  {
    id: "workspace-beta",
    catalog_id: "catalog-beta",
    organization_id: "org-123",
    workspace_id: "workspace-123",
  },
] satisfies AgentModelAccessRead[]

const workspace = {
  id: "workspace-123",
  name: "Test workspace",
  organization_id: "org-123",
} satisfies WorkspaceRead

function renderWorkspaceModelSettings() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const invalidateQueriesSpy = jest.spyOn(queryClient, "invalidateQueries")

  render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceModelSettings workspace={workspace} />
    </QueryClientProvider>
  )

  return { invalidateQueriesSpy }
}

describe("WorkspaceModelSettings", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(listCatalog).mockResolvedValue({
      items: catalogEntries,
      next_cursor: null,
    } satisfies AgentCatalogListResponse)
    jest.mocked(listEnabledModels).mockResolvedValue({
      items: accessRows,
      next_cursor: null,
    } satisfies AgentModelAccessListResponse)
    jest.mocked(listCustomProviders).mockResolvedValue({
      items: [],
      next_cursor: null,
    } satisfies AgentCustomProviderListResponse)
    jest.mocked(disableModel).mockResolvedValue(undefined)
    jest.mocked(enableModel).mockResolvedValue(accessRows[0])
  })

  it("keeps the save action visible in a sticky footer", async () => {
    renderWorkspaceModelSettings()

    const saveButton = await screen.findByRole("button", { name: "Save" })

    expect(saveButton.parentElement).toHaveClass("sticky", "bottom-0")
    expect(
      screen.getByRole("region", { name: "Organization-enabled models" })
    ).toHaveClass("h-80")
    expect(
      screen.getByRole("textbox", {
        name: "Search organization-enabled models",
      })
    ).toBeInTheDocument()
  })

  it("invalidates the effective workspace models after saving a subset", async () => {
    const user = userEvent.setup()
    const { invalidateQueriesSpy } = renderWorkspaceModelSettings()

    await screen.findByText("Alpha Model")
    await user.click(
      screen.getByRole("switch", {
        name: "Inherit organization-enabled models",
      })
    )
    await user.click(screen.getByRole("checkbox", { name: /Beta Model/ }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(disableModel).toHaveBeenCalledWith({ accessId: "workspace-beta" })
    })
    expect(enableModel).not.toHaveBeenCalled()
    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: ["workspace", "workspace-123", "agent-models"],
    })
  })
})
